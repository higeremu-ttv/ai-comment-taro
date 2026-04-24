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
配信者の名前は「{streamer_name}」です。

【自分の名前に関するルール】
- 「{ai_name}」と呼ばれたら自分への呼びかけと認識して返答すること
- 一人称は「自分」または「俺」を使うこと（「{ai_name}は〜」はNG）
- 「{ai_name}が良かった」など褒められたら素直に返すこと

【配信者への呼びかけ】
- 名前は基本的に使わない。自然な語尾（「だよ」「だよね」「じゃん」など）で対応する
- 20回に1回程度、文中や文末で自然に使うのはOK（例：「〜って{streamer_name}はどう思う？」「{streamer_name}的にはどうなんだろ」）
- コメントの冒頭に名前を付けることは絶対禁止（「{streamer_name}、〜」という書き出しは禁止）
- 「配信者」「あなた」という呼び方は禁止

【絶対に守るルール】
- 日本語のみで書くこと
- コメント本文だけを出力すること（前置き・注釈・ト書きは一切不要）
- 絵文字・特殊記号は使わないこと
- **必ず10文字以上の完結した文章で書くこと**
- 「え、」「やばい、」「なるほど、」など接続詞や感嘆詞だけで終わることは絶対禁止
- 必ず文章を最後まで書き切ること（途中で終わることは禁止）
- 出力はコメント本文のみ。「（〜に対して）」「自分:」などの注釈は絶対に含めないこと
- 「〇〇」「（配信者名）」などのプレースホルダーを使わないこと
- 性的・エロティックな表現は一切禁止
- 差別的・侮辱的・ヘイトスピーチに当たる表現は一切禁止
- 暴力的・攻撃的な表現は一切禁止
- Twitchの利用規約に違反する可能性がある表現は一切禁止

【Twitchコミュニティガイドライン遵守】
- 他者への嫌がらせ・ストーキング・脅迫は禁止
- 年齢・性別・人種・宗教・障害などに基づく差別は禁止
- 個人情報（本名・住所・電話番号等）の言及は禁止
- 自傷・自殺を促すような表現は禁止
- スパム的な繰り返し投稿は禁止

【オウム返し禁止】
- 配信者の発言をそのまま繰り返すことは絶対禁止
- 「〜って言ってたけど」「〜なんだね」「〜ってことか」など発言を繰り返すパターンは使わない
- 必ず自分の意見・感想・質問から始めること

【コメントの長さ・スタイル】
- 1〜3文で書くこと
- 友達に話しかけるような自然なタメ口（口語体）で書くこと
- 毎回異なる語尾・表現・切り口を使うこと（同じパターンの繰り返しは禁止）
- 語尾のバリエーション：「〜だよ」「〜じゃん」「〜だよね」「〜かな」「〜じゃない？」「〜だろ」「〜だと思う」「〜じゃないの」等
- 単なる相槌（「なるほど」だけ、「そうですね」だけ）は避けること
- 「感想・共感」＋「質問や意見」の2段構成が理想
- 質問の内容が具体的でなかったり、情報が足りなくて答えられない場合は、知ったかぶりをせず、友達口調で具体的に聞き返すこと"""

    def __init__(self, config):
        self.config = config
        self._last_comment_time = 0.0
        self._last_comments = []
        self._conversation_history = []
        self._gemini_model = None
        self.is_generating = False
        self.api_request_times = []
        self._silence_prompt_index = 0
        self._stream_info = {}  # Twitchから取得した配信情報

        # 成長型プロフィール
        try:
            from profile_manager import ProfileManager
            import os
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self._profile_manager = ProfileManager(base_dir)
            # 配信者名をプロフィールに設定
            streamer_name = getattr(config, 'STREAMER_NAME', '')
            if streamer_name:
                self._profile_manager.set_streamer_name(streamer_name)
        except Exception as e:
            logger.debug(f"プロフィールマネージャー初期化失敗: {e}")
            self._profile_manager = None

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

        # ヒアリング質問リスト（趣味・好み系のみ・個人情報系は除外）
        self.HEARING_QUESTIONS = [
            "好きな食べ物って何？",
            "好きな音楽とかある？",
            "フォートナイト以外で好きなゲームって何かある？",
            "休みの日って何してることが多いの？",
            "最近観た映画とかドラマって何かある？",
            "好きなスポーツとかある？",
            "コーヒーと紅茶どっち派？",
            "猫派？犬派？",
            "朝型？夜型？",
            "最近おいしかったもの何かある？",
        ]
        self._last_hearing_time = 0.0
        self._hearing_cooldown = 600  # ヒアリング質問は10分に1回まで

    def get_system_prompt(self) -> str:
        ai_name = getattr(self.config, 'AI_NAME', 'AIコメント太郎')
        streamer_name = getattr(self.config, 'STREAMER_NAME', '') or '配信者'
        prompt = self.SYSTEM_PROMPT_TEMPLATE.replace('{ai_name}', ai_name).replace('{streamer_name}', streamer_name)

        # 配信情報をプロンプトに追加
        if self._stream_info:
            stream_context = "\n【今日の配信情報】\n"
            if self._stream_info.get('title'):
                stream_context += f"- 配信タイトル: {self._stream_info['title']}\n"
            if self._stream_info.get('game_name'):
                stream_context += f"- プレイ中のゲーム: {self._stream_info['game_name']}\n"
            prompt += stream_context

        # 学習済みプロフィールを追加
        if self._profile_manager:
            profile_text = self._profile_manager.get_prompt_text()
            if profile_text:
                prompt += f"\n{profile_text}"

        return prompt

    def reset_history(self):
        # 配信終了時にプロフィールを更新
        if self._profile_manager and self._conversation_history:
            api_key = getattr(self.config, 'GEMINI_API_KEY', '')
            self._profile_manager.update_from_conversation(self._conversation_history, api_key)

        self._conversation_history = []
        self._last_comments = []
        self._conversation_state = ConversationState.IDLE
        self._current_topic_turns = 0
        self._gemini_model = None
        self._stream_info = {}
        logger.info("会話履歴とモデルをリセットしました")

    def set_stream_info(self, info: dict):
        """Twitchから取得した配信情報をセットする"""
        self._stream_info = info
        if info:
            logger.info(f"配信情報をセット: {info}")
        # モデルを再初期化してシステムプロンプトに反映
        self._gemini_model = None

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
                    # 性的コンテンツは中程度以上をブロック
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                ],
                system_instruction=self.get_system_prompt()
            )
            logger.info(f"Gemini APIモデルを初期化しました: {model_name}")
            return self._gemini_model
        except Exception as e:
            logger.error(f"Gemini APIの初期化エラー: {e}")
            return None

    def _is_search_trigger(self, text: str) -> bool:
        """検索が必要なキーワードが含まれているか判定する"""
        search_keywords = [
            '調べて', '検索して', '教えて', 'おしえて',
            'なんだっけ', 'なんだろう', 'どうやって', 'どうやる',
            'って何', 'とは何', 'とは？', 'って何？',
            'いくら', 'どこで', 'いつから', 'いつまで',
            '最新', '今の', '今日の', '天気', 'ニュース',
        ]
        return any(kw in text for kw in search_keywords)

    def _call_gemini_with_search(self, prompt: str) -> Optional[str]:
        """Google Search Groundingを有効にしてGemini APIを呼び出す"""
        try:
            import google.generativeai as genai
            from google.generativeai import protos
            genai.configure(api_key=self.config.GEMINI_API_KEY)

            # 検索Groundingはflash-liteが非対応のためflashを使用
            model = genai.GenerativeModel(
                model_name='gemini-2.5-flash',
                system_instruction=self.get_system_prompt()
            )

            # Google Search toolをv0.8.x対応の形式で指定
            search_tool = protos.Tool(
                google_search_retrieval=protos.GoogleSearchRetrieval()
            )

            self.is_generating = True
            search_prompt = prompt + "\n\n必要なら検索して調べてください。回答は日本語で20文字以内の短い友達口調でお願いします。"
            response = model.generate_content(
                search_prompt,
                tools=[search_tool]
            )

            if not response or not response.text:
                return None

            comment = response.text.strip().replace("\n", " ").strip()
            if len(comment) < 5:
                return None

            logger.info(f"[検索モード] 回答生成成功: {comment[:50]}")
            return comment

        except Exception as e:
            logger.warning(f"[検索grounding] 失敗、通常モードにフォールバック: {e}")
            return None
        finally:
            self.is_generating = False

    def _call_gemini(self, prompt: str, use_search: bool = False) -> Optional[str]:
        """Gemini APIを呼び出す。use_search=TrueのときはGrounding有効。"""
        if use_search:
            result = self._call_gemini_with_search(prompt)
            if result:
                return result
            # 失敗した場合は通常モードにフォールバック
            logger.info("[検索grounding] 通常モードにフォールバック")

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
                logger.warning("Gemini APIのレート制限に達しました。10秒待機します。")
                time.sleep(10)
            else:
                logger.error(f"コメント生成エラー: {e}")
            return None
        finally:
            self.is_generating = False

    def _record_comment(self, comment: str):
        self._last_comments.append(comment)
        if len(self._last_comments) > 30:
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
            # 検索キーワードが含まれていたら検索モード
            if self._needs_search(speech_text):
                result = self._call_gemini_with_search(
                    f"「{speech_text}」という質問に対して、検索結果を元に20文字以内で友達口調で簡潔に答えてください。情報が足りない場合は知ったかぶりせず友達口調で聞き返してください。"
                )
                if result:
                    return result
            return self._generate_direct_conversation(speech_text)
        if trigger == CommentTrigger.VIEWER_COMMAND:
            # 検索キーワードが含まれていたら検索モード
            if self._needs_search(speech_text):
                result = self._call_gemini_with_search(
                    f"「{speech_text}」という質問に対して、検索結果を元に20文字以内で友達口調で簡潔に答えてください。情報が足りない場合は知ったかぶりせず友達口調で聞き返してください。"
                )
                if result:
                    return result
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
        # 音声認識結果にNGワードが含まれていたらGeminiに送らずスキップ
        if self._contains_ng_word(speech_text):
            logger.warning(f"[入力NGワード] 音声認識結果にポリシー違反の可能性があるためスキップ")
            return None

        # ヒアリング質問への回答をプロフィールに記録
        if self._profile_manager and (time.time() - self._last_hearing_time) < 120:
            # 直前のヒアリング質問から2分以内の発言は回答として記録
            self._profile_manager.add_topic(f"好み: {speech_text[:20]}")
            logger.info(f"[ヒアリング記録] {speech_text[:30]}")

        # 検索キーワードが含まれていたら検索モードで回答
        if self._is_search_trigger(speech_text):
            logger.info(f"[検索モード] '{speech_text}' に検索キーワードを検出")
            result = self._call_gemini(
                f"配信者が「{speech_text}」と言いました。検索結果を元に20文字以内で友達口調で簡潔に答えてください。情報が足りない場合は知ったかぶりせず友達口調で聞き返してください。",
                use_search=True
            )
            if result:
                self._record_comment(result)
                return result

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

        # ヒアリング質問（10分に1回・沈黙180秒以上のとき）
        now = time.time()
        if silence_seconds >= 180 and (now - self._last_hearing_time) >= self._hearing_cooldown:
            import random
            question = random.choice(self.HEARING_QUESTIONS)
            prompt = f"""{history_text}配信者が{int(silence_seconds)}秒間無言です。
視聴者として配信者のことをもっと知りたいので、以下の質問を自然な友達口調で聞いてください。
質問：「{question}」
必ず1文で、文章を最後まで書き切ること。"""
            comment = self._call_gemini(prompt)
            if comment and not self._is_duplicate(comment):
                self._record_comment(comment)
                self._last_hearing_time = now
                logger.info(f"[ヒアリング質問] {comment}")
                return comment

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
            comment = self._call_gemini(prompt)
        else:
            question = content.strip() if content.strip() else '何か話して'
            history_text = self._build_history_text()
            prompt = f"""{history_text}視聴者からの質問：「{question}」
自然に答えてください。1〜3文、日本語のみ。"""
            # 質問に検索キーワードが含まれていたら検索モードで回答
            use_search = self._is_search_trigger(question)
            if use_search:
                logger.info(f"[検索モード] 視聴者コマンドで検索キーワードを検出: {question}")
            comment = self._call_gemini(prompt, use_search=use_search)

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
