"""
コメント生成モジュール v3.1

【設計方針】
- Geminiが返したテキストは基本そのまま出す（補完・検証ロジックなし）
- 日本語が含まれていればOK（最低限チェックのみ）
- 会話ステート管理あり（話題→深掘り→着地）
- 聞き取り失敗コメントなし
- VC指示語フィルターなし
- 途中切れ補完なし

【残した機能】
- 発言への反応コメント（会話ステート管理つき）
- 無言時の話しかけ
- 画面認識コメント
- AI名前呼びかけへの直接返答
- クールダウン管理
- 重複コメント防止
"""

import logging
import time
import re
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)


class CommentTrigger(Enum):
    SPEECH_RESPONSE = "speech_response"
    SILENCE_BREAKER = "silence_breaker"
    SCREEN_EVENT = "screen_event"
    DIRECT_CONVERSATION = "direct_conversation"
    VIEWER_COMMAND = "viewer_command"
    UNRECOGNIZED_SPEECH = "unrecognized_speech"


class ConversationState(Enum):
    IDLE = "idle"
    TOPIC_RAISED = "topic_raised"
    DEEPENING = "deepening"
    LANDING = "landing"
    COOLDOWN = "cooldown"


class CommentGenerator:

    SYSTEM_PROMPT_TEMPLATE = """あなたはTwitchのゲーム配信を毎日見ている日本人の常連視聴者です。
配信者とは気心の知れた仲で、リアルな友達のように自然に会話します。

あなたの名前は「{ai_name}」です。

【自分の名前に関するルール】
- 「{ai_name}」と呼ばれたら自分への呼びかけと認識して返答すること
- 一人称は「自分」または「俺」を使うこと（「{ai_name}は〜」はNG）
- 「{ai_name}が良かった」など褒められたら素直に返すこと

【絶対に守るルール】
- 日本語のみで書くこと
- コメント本文だけを出力すること（前置き・注釈・ト書きは一切不要）
- 絵文字・特殊記号は使わないこと
- **必ず10文字以上の完結した文章で書くこと**
- 「え、」「やばい、」「なるほど、」など接続詞や感嘆詞だけで終わることは絶対禁止
- 必ず文章を最後まで書き切ること（途中で終わることは禁止）
- 出力はコメント本文のみ。「（〜に対して）」「自分:」などの注釈は絶対に含めないこと
- 配信者の名前がわからない場合は「〇〇」「（配信者名）」などのプレースホルダーを使わないこと。「あなた」「配信者」「そっち」などで代替すること

【コメントの長さ・スタイル】
- 1〜3文で書くこと
- 友達に話しかけるような自然なタメ口（口語体）で書くこと
- 語尾に変化をつけること（「〜だよ」「〜じゃん」「〜だよね」「〜かな」等）
- 単なる相槌（「なるほど」だけ、「そうですね」だけ）は避けること
- 「感想・共感」＋「質問や意見」の2段構成が理想"""

    def __init__(self, config):
        self.config = config
        self._last_comment_time = 0.0
        self._last_comments = []
        self._conversation_history = []
        self._gemini_model = None
        self.is_generating = False
        self.api_request_times = []
        self._silence_prompt_index = 0

        # 会話ステート
        self._conversation_state = ConversationState.IDLE
        self._current_topic_turns = 0
        self._topic_start_time = 0.0
        self._topic_end_time = 0.0
        self._max_turns = getattr(config, 'CONVERSATION_MAX_TURNS', 3)
        self._topic_cooldown = getattr(config, 'TOPIC_COOLDOWN_SECONDS', 60)

        self.SILENCE_PROMPTS = [
            "今どんな状況なの？",
            "今何考えてるか教えてよ",
            "さっきのプレイどう思った？",
            "今日の調子はどう？",
            "今の作戦を教えてよ",
            "このゲームで一番難しいところってどこ？",
            "今日の目標は達成できそう？",
            "最近ハマってることって何かある？",
            "このゲーム、何が一番面白い？",
            "今日ここまでで一番よかった瞬間は？",
        ]

    def get_system_prompt(self) -> str:
        ai_name = getattr(self.config, 'AI_NAME', '太郎')
        return self.SYSTEM_PROMPT_TEMPLATE.replace('{ai_name}', ai_name)

    def reset_history(self):
        self._conversation_history = []
        self._last_comments = []
        self._conversation_state = ConversationState.IDLE
        self._current_topic_turns = 0
        self._gemini_model = None
        logger.info("会話履歴とモデルをリセットしました")

    def _get_gemini_model(self):
        if self._gemini_model is not None:
            return self._gemini_model
        try:
            import google.generativeai as genai
            api_key = getattr(self.config, 'GEMINI_API_KEY', '')
            if not api_key or api_key == 'your_gemini_api_key_here':
                logger.error("Gemini APIキーが設定されていません。")
                return None
            genai.configure(api_key=api_key)
            model_name = getattr(self.config, 'GEMINI_MODEL', 'gemini-1.5-flash')
            max_tokens = getattr(self.config, 'COMMENT_MAX_TOKENS', 120)
            self._gemini_model = genai.GenerativeModel(
                model_name=model_name,
                generation_config=genai.GenerationConfig(
                    temperature=0.9,
                    top_p=0.92,
                    top_k=50,
                    max_output_tokens=max_tokens,
                ),
                safety_settings=[
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                ],
                system_instruction=self.get_system_prompt()
            )
            logger.info(f"Gemini APIモデルを初期化しました: {model_name}")
            return self._gemini_model
        except Exception as e:
            logger.error(f"Gemini APIの初期化エラー: {e}")
            return None

    def _call_gemini(self, prompt: str) -> Optional[str]:
        """Gemini APIを呼び出す。短すぎる結果は1回だけ再試行する。"""
        for attempt in range(2):
            result = self._call_gemini_once(prompt)
            if result is not None:
                return result
            if attempt == 0:
                logger.debug("1回目の生成が短すぎたか失敗。再試行します...")
        return None

    def _call_gemini_once(self, prompt: str) -> Optional[str]:
        """Gemini APIを1回呼び出す。"""
        try:
            model = self._get_gemini_model()
            if model is None:
                return None

            self.is_generating = True
            now = time.time()
            self.api_request_times = [t for t in self.api_request_times if now - t < 60]
            self.api_request_times.append(now)

            response = model.generate_content(prompt)

            if not response or not response.text:
                logger.warning("Gemini APIから空のレスポンスが返りました")
                return None

            comment = response.text.strip()
            comment = comment.replace("\n", " ").strip()

            # プロンプト漏れの最低限の除去
            comment = re.sub(r'^（[^）]{1,30}）\s*', '', comment).strip()
            comment = re.sub(r'^(自分|bot|視聴者bot|あなた)\s*[:：]\s*', '', comment).strip()

            if not comment:
                return None

            # 日本語が1文字以上含まれているか
            if not any('\u3040' <= c <= '\u9FFF' for c in comment):
                logger.warning(f"日本語なしのコメントを破棄: {repr(comment)}")
                return None

            # 短すぎるコメントは破棄（10文字未満）
            if len(comment) < 10:
                logger.warning(f"短すぎるコメント({len(comment)}文字)を破棄: '{comment}'")
                return None

            # 生成されたコメントにNGワードが含まれていたら破棄
            if self._contains_ng_word(comment):
                return None

            return comment

        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "quota" in error_str.lower() or "rate" in error_str.lower():
                logger.warning("Gemini APIのレート制限に達しました。スキップします。")
            else:
                logger.error(f"コメント生成エラー: {e}")
            return None
        finally:
            self.is_generating = False

    def _record_comment(self, comment: str):
        self._last_comments.append(comment)
        if len(self._last_comments) > 10:
            self._last_comments.pop(0)
        self._last_comment_time = time.time()
        self._conversation_history.append({"role": "bot", "content": comment})
        if len(self._conversation_history) > 20:
            self._conversation_history.pop(0)

    def _is_duplicate(self, comment: str) -> bool:
        return comment in self._last_comments

    def _contains_ng_word(self, text: str) -> bool:
        """NGワードが含まれているかチェックする"""
        ng_words_str = getattr(self.config, 'NG_WORDS', '')
        if not ng_words_str:
            return False
        ng_words = [w.strip() for w in ng_words_str.split(',') if w.strip()]
        for word in ng_words:
            if word in text:
                logger.warning(f"[NGワード検出] '{word}' が含まれているためスキップします")
                return True
        return False

    def _build_history_text(self) -> str:
        if len(self._conversation_history) <= 1:
            return ""
        history_text = "【これまでの会話の流れ】\n"
        for msg in self._conversation_history[-6:]:
            role_name = "配信者" if msg["role"] == "streamer" else "あなた"
            history_text += f"{role_name}: {msg['content']}\n"
        return history_text + "\n"

    def _is_in_topic_cooldown(self) -> bool:
        if self._conversation_state != ConversationState.COOLDOWN:
            return False
        elapsed = time.time() - self._topic_end_time
        if elapsed >= self._topic_cooldown:
            self._conversation_state = ConversationState.IDLE
            logger.info(f"話題クールダウン終了（{elapsed:.0f}秒経過）")
            return False
        return True

    def _update_conversation_state(self, speech_text: str = ""):
        """会話ステートを更新する"""
        if self._conversation_state == ConversationState.IDLE:
            self._conversation_state = ConversationState.TOPIC_RAISED
            self._current_topic_turns = 1
            self._topic_start_time = time.time()
        elif self._conversation_state == ConversationState.TOPIC_RAISED:
            self._conversation_state = ConversationState.DEEPENING
            self._current_topic_turns += 1
        elif self._conversation_state == ConversationState.DEEPENING:
            self._current_topic_turns += 1
            if self._current_topic_turns >= self._max_turns:
                logger.info(f"最大往復回数（{self._max_turns}回）に達しました。話題を締めます。")
                self._conversation_state = ConversationState.LANDING

    def generate(self, trigger: CommentTrigger, speech_text: str = "",
                 screen_situation: str = "", silence_seconds: float = 0,
                 username: str = "") -> Optional[str]:

        # AI名前呼びかけ・視聴者コマンドはクールダウンをバイパス
        if trigger == CommentTrigger.DIRECT_CONVERSATION:
            return self._generate_direct_conversation(speech_text)
        if trigger == CommentTrigger.VIEWER_COMMAND:
            return self._generate_viewer_command_response(speech_text)

        # 音声テキストにNGワードが含まれていたらスキップ
        if speech_text and self._contains_ng_word(speech_text):
            return None

        # 話題クールダウン中はスキップ
        if self._is_in_topic_cooldown():
            remaining = self._topic_cooldown - (time.time() - self._topic_end_time)
            logger.debug(f"話題クールダウン中 ({remaining:.0f}秒残り)")
            return None

        # コメントクールダウンチェック
        elapsed = time.time() - self._last_comment_time
        if elapsed < self.config.COMMENT_COOLDOWN_SECONDS:
            logger.debug(f"クールダウン中 ({elapsed:.0f}秒 / {self.config.COMMENT_COOLDOWN_SECONDS}秒)")
            return None

        if trigger == CommentTrigger.SPEECH_RESPONSE:
            return self._generate_speech_response(speech_text, screen_situation)
        elif trigger == CommentTrigger.SILENCE_BREAKER:
            return self._generate_silence_breaker(screen_situation, silence_seconds)
        elif trigger == CommentTrigger.SCREEN_EVENT:
            return self._generate_screen_reaction(screen_situation)
        elif trigger == CommentTrigger.UNRECOGNIZED_SPEECH:
            return None  # 聞き取り失敗コメントは出さない
        return None

    def _generate_speech_response(self, speech_text: str, screen_description: str) -> Optional[str]:
        self._conversation_history.append({"role": "streamer", "content": speech_text})
        if len(self._conversation_history) > 20:
            self._conversation_history.pop(0)

        # 着地ステートの場合は締めのコメント
        if self._conversation_state == ConversationState.LANDING:
            comment = self._generate_landing_comment(speech_text)
            if comment:
                self._record_comment(comment)
                self._conversation_state = ConversationState.COOLDOWN
                self._topic_end_time = time.time()
                self._current_topic_turns = 0
                logger.info(f"[着地] {comment} → {self._topic_cooldown}秒クールダウン開始")
                return comment
            return None

        # ステートを更新
        self._update_conversation_state(speech_text)

        history_text = self._build_history_text()

        screen_context = ""
        if screen_description and "ゲーム画面が表示されていません" not in screen_description and "配信が始まったばかり" not in screen_description:
            screen_context = f"\n【現在のゲーム画面】\n{screen_description}\n"

        # ステートに応じてプロンプトを変える
        if self._conversation_state == ConversationState.DEEPENING:
            style_hint = f"この話題はすでに{self._current_topic_turns}往復しています。深掘り質問か自分の意見を述べてください。"
        else:
            style_hint = "深掘り質問・共感・ツッコミ・自分の意見など自由なスタイルで返してください。"

        prompt = f"""{history_text}【配信者の発言】
「{speech_text}」
{screen_context}
※音声認識のため誤変換や途切れがある場合があります。文脈から意図を推測して返答してください。

{style_hint}
自然な日本語で1〜2文で返答してください。
【重要】必ず20文字以上の完結した文章で書くこと。「え、」「やばい、」など途中で終わることは禁止。"""

        comment = self._call_gemini(prompt)
        if comment and not self._is_duplicate(comment):
            self._record_comment(comment)
            logger.info(f"[発言反応] コメント生成 (ステート:{self._conversation_state.value}, {self._current_topic_turns}往復): {comment}")
            return comment
        return None

    def _generate_landing_comment(self, speech_text: str) -> Optional[str]:
        history_text = self._build_history_text()
        prompt = f"""{history_text}【配信者の発言】
「{speech_text}」

この話題はそろそろ終わりにします。
会話を自然に締めくくるコメントを1〜2文で書いてください。
例：「なるほど、よくわかったよ。また話しかけてね」「面白い話だったな、楽しかったよ」
【重要】必ず20文字以上の完結した文章で書くこと。"""
        return self._call_gemini(prompt)

    def _generate_silence_breaker(self, screen_description: str, silence_seconds: float) -> Optional[str]:
        # IDLE状態に戻す
        if self._conversation_state == ConversationState.IDLE:
            self._conversation_state = ConversationState.TOPIC_RAISED
            self._current_topic_turns = 0
            self._topic_start_time = time.time()

        history_text = self._build_history_text()

        screen_context = ""
        if screen_description and "ゲーム画面が表示されていません" not in screen_description and "配信が始まったばかり" not in screen_description:
            screen_context = f"\n【現在のゲーム画面】\n{screen_description}\n"

        # 直前の発言があれば深掘り
        last_streamer_speech = None
        for msg in reversed(self._conversation_history):
            if msg["role"] == "streamer":
                last_streamer_speech = msg["content"]
                break

        if last_streamer_speech and silence_seconds < 180:
            prompt = f"""{history_text}配信者がしばらく無言です。
直前に「{last_streamer_speech}」と言っていました。{screen_context}
この発言についてさらに深掘りする質問か、関連した話題を振るコメントを1文で書いてください。
必ず文章を最後まで書き切ること。"""
        else:
            topic = self.SILENCE_PROMPTS[self._silence_prompt_index % len(self.SILENCE_PROMPTS)]
            self._silence_prompt_index += 1
            prompt = f"""{history_text}配信者が{int(silence_seconds)}秒間無言です。{screen_context}
視聴者として話しかけるコメントを1文で書いてください。
テーマのヒント：「{topic}」
必ず文章を最後まで書き切ること。"""

        comment = self._call_gemini(prompt)
        if comment and not self._is_duplicate(comment):
            self._record_comment(comment)
            logger.info(f"[無言打破] {comment}")
            return comment
        return None

    def _generate_screen_reaction(self, screen_description: str) -> Optional[str]:
        if not screen_description or "ゲーム画面が表示されていません" in screen_description:
            return None

        prompt = f"""ゲーム配信でこんな状況が起きています：
{screen_description}

この状況を見た視聴者として、自然な日本語コメントを1文で書いてください。
必ず文章を最後まで書き切ること。"""

        comment = self._call_gemini(prompt)
        if comment and not self._is_duplicate(comment):
            self._record_comment(comment)
            logger.info(f"[画面反応] {comment}")
            return comment
        return None

    def _generate_direct_conversation(self, speech_text: str) -> Optional[str]:
        ai_name = getattr(self.config, 'AI_NAME', '太郎')
        history_text = self._build_history_text()
        self._conversation_history.append({"role": "streamer", "content": speech_text})
        if len(self._conversation_history) > 20:
            self._conversation_history.pop(0)

        prompt = f"""{history_text}【配信者から{ai_name}への直接の呼びかけ】
「{speech_text}」

配信者が直接あなた（{ai_name}）に話しかけています。
自分の言葉でしっかり答えてください。
必ず日本語のみで、文章を最後まで書き切ること。"""

        comment = self._call_gemini(prompt)
        if comment:
            self._record_comment(comment)
            self._last_comment_time = time.time()
            logger.info(f"[直接会話] {comment}")
            return comment
        return None

    def _generate_viewer_command_response(self, speech_text: str) -> Optional[str]:
        ai_name = getattr(self.config, 'AI_NAME', '太郎')
        if ':' in speech_text:
            cmd_type, content = speech_text.split(':', 1)
        else:
            cmd_type, content = 'ask', speech_text

        if cmd_type == 'hello':
            username = content.strip() if content.strip() else '視聴者'
            prompt = f"Twitchの視聴者「{username}」さんが挨拶してくれました。フレンドリーに返してください。1〜2文、日本語のみ。"
        else:
            question = content.strip() if content.strip() else '何か話して'
            history_text = self._build_history_text()
            prompt = f"""{history_text}視聴者からの質問：「{question}」
自然に答えてください。1〜3文、日本語のみ。"""

        comment = self._call_gemini(prompt)
        if comment:
            self._record_comment(comment)
            self._last_comment_time = time.time()
            logger.info(f"[視聴者コマンド:{cmd_type}] {comment}")
            return comment
        return None

    def check_direct_call(self, speech_text: str) -> bool:
        ai_name = getattr(self.config, 'AI_NAME', '太郎')
        if not ai_name:
            return False
        patterns = [
            f"{ai_name}、", f"{ai_name}，", f"{ai_name} ",
            f"{ai_name}！", f"{ai_name}!", f"{ai_name}？", f"{ai_name}?",
            f"ねえ{ai_name}", f"ねえ、{ai_name}", f"おい{ai_name}",
            f"{ai_name}くん", f"{ai_name}ちゃん", f"{ai_name}さん",
        ]
        return any(p in speech_text for p in patterns)

    # 後方互換性
    def generate_speech_response(self, speech_text: str, screen_description: str) -> Optional[str]:
        return self._generate_speech_response(speech_text, screen_description)

    def generate_silence_breaker(self, screen_description: str, silence_seconds: float) -> Optional[str]:
        return self._generate_silence_breaker(screen_description, silence_seconds)

    def generate_screen_reaction(self, screen_description: str, previous_description: str = "") -> Optional[str]:
        return self._generate_screen_reaction(screen_description)
