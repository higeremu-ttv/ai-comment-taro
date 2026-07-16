"""
コメント生成モジュール v4.10

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

【キャラクターについて】
あなたは多面的な性格を持っています。以下のモードが自然に混在します：
- 真面目モード：ゲームの状況や話題に的確にコメントする
- オヤジモード：会話の流れでダジャレ・オヤジギャグを自然に挟む
- 流行語モード：「それな」「草」「エモい」「神ってる」「わかりみ」などを自然に使う
これらは切り替えるのではなく、一つの会話の中に自然に混在させること。

【オヤジギャグについて】
5回に1回程度、ダジャレやオヤジギャグを自然に会話に混ぜること。
例：
- 「豆の話か〜、まめに来てくれてるね（笑）」
- 「パルスライフルか、パルスが上がるね！」
- 「宝箱か〜、たからものにするよ！」
- 「ロケランか、ろけっとスタートだね！」
強引でもいい。スベっても味があっていい。むしろスベり気味の方がオヤジらしくていい。

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

【事実の捏造禁止】
- 配信者や視聴者の過去の出来事・持ち物・家族・ペットなどを、勝手に作って「あったこと」のように語ることは絶対禁止（例：「昔飼ってた犬の名前じゃない？」のような創作）
- 事実として語ってよいのは、手帳メモや会話の流れに実際に出てきたことだけ。それ以外は決めつけずに聞く側に回ること

【聞き取れない言葉への対応】
- 配信者の発言は音声認識のため、意味の通らない言葉が混ざることがある
- 意味の通らない言葉を「」で引用して「〜って何？」「〜って何だろう？」と聞き返すパターンは禁止（繰り返すと会話が成立していないように見える）
- 意味の通る部分にだけ反応すること。発言全体が意味不明なら、ゲームの状況や直前の話題への軽い一言にとどめること

【視聴者の呼び方】
- 視聴者に触れるときは、手帳メモにある呼び名だけを使うこと
- 英数字のID（ユーザー名）をコメントにそのまま書くことは絶対禁止。呼び名が分からないときは名前に触れずに書くこと

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
        self._gemini_models = {}  # v4.30: モデル名→初期化済みモデルのキャッシュ
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
        self._gemini_models = {}
        self._stream_info = {}
        logger.info("会話履歴とモデルをリセットしました")

    def reset_conversation_state(self):
        self._conversation_state = ConversationState.IDLE
        self._current_topic_turns = 0
        self._topic_end_time = 0.0
        logger.info("会話ステートをリセットしました")

    def set_stream_info(self, info: dict):
        """Twitchから取得した配信情報をセットする"""
        self._stream_info = info
        if info:
            logger.info(f"配信情報をセット: {info}")
        # モデルを再初期化してシステムプロンプトに反映
        self._gemini_models = {}

    def _max_tokens_for(self, model_name: str) -> int:
        """モデルごとの出力トークン予算を返す（v4.41）。
        会話用モデル（2.5 Flash以上）は思考型で、答える前の「頭の中の下書き」も
        予算を消費するため大きめに取る。相槌用Liteは従来通り小さくてよい。"""
        lite_model = getattr(self.config, 'GEMINI_MODEL', 'gemini-2.5-flash-lite')
        if model_name and model_name != lite_model:
            return getattr(self.config, 'SMART_MAX_TOKENS', 2048)
        return getattr(self.config, 'COMMENT_MAX_TOKENS', 300)

    def _get_gemini_model(self, model_name: str = ""):
        """指定モデルを取得する（v4.30: モデル名ごとにキャッシュ）"""
        if not model_name:
            model_name = getattr(self.config, 'GEMINI_MODEL', 'gemini-2.5-flash-lite')
        if model_name in self._gemini_models:
            return self._gemini_models[model_name]
        try:
            import google.generativeai as genai
            api_key = getattr(self.config, 'GEMINI_API_KEY', '')
            if not api_key or api_key == 'your_gemini_api_key_here':
                logger.error("Gemini APIキーが設定されていません。")
                return None
            genai.configure(api_key=api_key)
            max_tokens = self._max_tokens_for(model_name)  # v4.41: モデル別の予算
            self._gemini_models[model_name] = genai.GenerativeModel(
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
            return self._gemini_models[model_name]
        except Exception as e:
            logger.error(f"Gemini APIの初期化エラー: {e}")
            return None

    def _get_external_client(self):
        """外部AI（OpenAI互換）クライアントを取得する（v4.40）。
        SMART_PROVIDERが"openai"以外のときはNone（＝Geminiを使う）。"""
        if not hasattr(self, '_external_client_cached'):
            try:
                from llm_client import build_smart_client
                self._external_client_cached = build_smart_client(self.config)
                if self._external_client_cached is not None:
                    logger.info(f"会話用に外部AIを使用します: {self._external_client_cached.model}"
                                f"（{self._external_client_cached.base_url}）")
            except Exception as e:
                logger.debug(f"外部AIクライアント初期化失敗: {e}")
                self._external_client_cached = None
        return self._external_client_cached

    def _call_gemini(self, prompt: str, smart: bool = False,
                     require_ending: bool = True) -> Optional[str]:
        """AIを呼び出す。

        v4.30: smart=True で会話用の上位モデル（GEMINI_MODEL_SMART）を使う。
        v4.40: SMART_PROVIDER="openai" なら会話をOpenAI互換の外部AIに出す。
        v4.42: require_ending=False で尻切れ検問を免除（俳句・謎かけ用。
               句点で終わらないのが正しい出力のため）
        どの経路でも失敗時は相槌用のGemini Liteに退避するので、コメントは止まらない。
        """
        lite_model = getattr(self.config, 'GEMINI_MODEL', 'gemini-2.5-flash-lite')
        smart_model = getattr(self.config, 'GEMINI_MODEL_SMART', '') or lite_model

        if smart:
            # v4.40: 外部AI（OpenAI/OpenRouter/ローカルLLM等）が設定されていれば最優先
            external = self._get_external_client()
            if external is not None:
                raw = external.chat(
                    self.get_system_prompt(), prompt,
                    max_tokens=getattr(self.config, 'COMMENT_MAX_TOKENS', 300),
                )
                comment = self._postprocess_comment(raw, require_ending) if raw else None
                if comment is not None:
                    return comment
                logger.warning(f"外部AI({external.model})での生成に失敗。{lite_model}に退避します")
                return self._call_gemini_once(prompt, model_name=lite_model,
                                              require_ending=require_ending)

            # v4.30: Geminiの上位モデル
            if smart_model != lite_model:
                result = self._call_gemini_once(prompt, model_name=smart_model,
                                                require_ending=require_ending)
                if result is not None:
                    return result
                logger.warning(f"会話用モデル({smart_model})での生成に失敗。{lite_model}に退避します")
                return self._call_gemini_once(prompt, model_name=lite_model,
                                              require_ending=require_ending)

        for attempt in range(2):
            result = self._call_gemini_once(prompt, model_name=lite_model,
                                            require_ending=require_ending)
            if result is not None:
                return result
            if attempt == 0:
                logger.debug("1回目の生成が短すぎたか失敗。再試行します...")
        return None

    def _call_gemini_once(self, prompt: str, model_name: str = "",
                          require_ending: bool = True) -> Optional[str]:
        """Gemini APIを1回呼び出す。"""
        try:
            model = self._get_gemini_model(model_name)
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

            # v4.40: 検査・整形は共通部品（外部AIと同じチェックを通す）
            return self._postprocess_comment(response.text, require_ending)

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

    def _call_gemini_raw(self, prompt: str, model_name: str = "") -> Optional[str]:
        """整形・検査なしでAIの出力をそのまま返す（v4.50・情報抽出用）。
        取材の答えから呼び名を抜く・「覚えて」の内容抽出などの事務作業用。
        既定は相槌用のLite（事務作業に高いモデルは不要）。"""
        try:
            model = self._get_gemini_model(model_name)
            if model is None:
                return None
            now = time.time()
            self.api_request_times = [t for t in self.api_request_times if now - t < 60]
            self.api_request_times.append(now)
            response = model.generate_content(prompt)
            if not response or not response.text:
                return None
            return response.text.strip()
        except Exception as e:
            logger.debug(f"抽出用の生成に失敗: {e}")
            return None

    def generate_search_answer(self, question: str) -> Optional[str]:
        """Google検索して答える（v4.50・検索機能の復活）。
        失敗したらNone（呼び出し側が通常会話に退避する）。"""
        try:
            from llm_client import gemini_search_answer
        except ImportError:
            return None
        api_key = getattr(self.config, 'GEMINI_API_KEY', '')
        model = (getattr(self.config, 'GEMINI_MODEL_SMART', '')
                 or getattr(self.config, 'GEMINI_MODEL', 'gemini-2.5-flash'))
        system_prompt = (self.get_system_prompt()
                         + "\n※今回はGoogle検索の結果を根拠に、正確な情報を2〜3文で伝えること。"
                           "検索で分からなかったことは知ったかぶりせず「見つからなかった」と言うこと。")
        raw = gemini_search_answer(api_key, model, system_prompt, question)
        comment = self._postprocess_comment(raw) if raw else None
        if comment:
            self._record_comment(comment)
            logger.info(f"[検索回答] {comment}")
        return comment

    def _postprocess_comment(self, raw: str, require_ending: bool = True) -> Optional[str]:
        """AIの生の出力を検査・整形する（v4.40で共通部品化）。
        Geminiでも外部AIでも、同じ品質チェック（日本語・長さ・NGワード・
        プロンプト漏れ）を通ってからコメントになる。
        v4.42: require_ending=False で尻切れ検問を免除（俳句・謎かけ用）"""
        if not raw:
            return None

        comment = raw.strip()
        comment = comment.replace("\n", " ").strip()

        # プロンプト漏れの最低限の除去
        comment = re.sub(r'^（[^）]{1,30}）\s*', '', comment).strip()
        comment = re.sub(r'^(自分|bot|視聴者bot|あなた)\s*[:：]\s*', '', comment).strip()

        if not comment:
            return None

        # 日本語が1文字以上含まれているか
        if not any('぀' <= c <= '鿿' for c in comment):
            logger.warning(f"日本語なしのコメントを破棄: {repr(comment)}")
            return None

        # 短すぎるコメントは破棄（10文字未満）
        if len(comment) < 10:
            logger.warning(f"短すぎるコメント({len(comment)}文字)を破棄: '{comment}'")
            return None

        # 尻切れチェック（v4.41）: 文の途中で切れた返答を破棄する
        # 思考型モデルが予算切れで途中終了した場合の保険。破棄すれば
        # 呼び出し側が自動でLiteに退避するので、尻切れ文が配信に出ることはない
        # v4.42: 俳句・謎かけは句点なしで終わるのが正しいため require_ending=False で免除
        if require_ending:
            valid_endings = tuple('。．！？!?…♪〜～」』）)ｗw草')
            if not comment.endswith(valid_endings):
                logger.warning(f"[尻切れ検出] 文が完結していないため破棄: '...{comment[-20:]}'")
                return None

        # v4.55: 冒頭の呼びかけ名を除去（「おじさん、〜」で始めるのは禁止ルールだが
        # 指示だけでは漏れるため、機械的に取り除く）
        streamer_name = getattr(self.config, 'STREAMER_NAME', '')
        if streamer_name:
            stripped = re.sub(rf'^{re.escape(streamer_name)}\s*[、，,]\s*', '', comment)
            if stripped != comment:
                if len(stripped) >= 10:
                    comment = stripped
                else:
                    logger.warning(f"[冒頭名検問] 名前を除くと短すぎるため破棄: '{comment}'")
                    return None

        # 生成されたコメントにNGワードが含まれていたら破棄
        if self._contains_ng_word(comment):
            return None

        # 内部プロンプト（命令文）がそのまま漏れていたら破棄
        if self._looks_like_prompt_leak(comment):
            return None

        # v4.55: 聞き返しオウム検問。音声認識が崩れた単語を引用して「〜って何？」と
        # 聞き返すコメントを破棄する（7/17配信で10回以上発生。プロンプトの指示では
        # 直らなかったため機械的に止める。破棄すれば呼び出し側が再生成する）
        if self._looks_like_parrot_question(comment):
            return None

        return comment

    # 聞き返しオウムのパターン（v4.55）
    # 1. 「◯◯」って何？ / 「◯◯」ってどういうこと？（かぎ括弧で引用して聞き返す）
    # 2. カタカナ4文字以上の（たいてい誤認識の）単語＋って何？
    PARROT_QUESTION_PATTERNS = [
        r'「[^」]{2,30}」\s*って、?\s*(何|なに|一体|どういうこと)',
        r'[ァ-ヴー・]{4,}\s*って、?\s*何(？|\?|だろう|かな|なんだ)',
    ]

    def _looks_like_parrot_question(self, comment: str) -> bool:
        for pat in self.PARROT_QUESTION_PATTERNS:
            if re.search(pat, comment):
                logger.warning(f"[聞き返し検問] 崩れた単語への聞き返しのため破棄: '{comment[:40]}'")
                return True
        return False

    def _record_comment(self, comment: str):
        self._last_comments.append(comment)
        if len(self._last_comments) > 30:
            self._last_comments.pop(0)
        self._last_comment_time = time.time()
        self._conversation_history.append({"role": "bot", "content": comment})
        if len(self._conversation_history) > 20:
            self._conversation_history.pop(0)

    @staticmethod
    def _char_bigrams(text: str) -> set:
        """文字2グラムの集合を返す（類似判定用・v4.11）"""
        s = re.sub(r'[\s　]+', '', text)
        return {s[i:i + 2] for i in range(len(s) - 1)}

    def _is_similar(self, a: str, b: str, threshold: float = 0.45) -> bool:
        """2つの文が「ほぼ同じ」かを判定する（v4.11）。
        v4.10までは完全一致のみで、スペース1個違いの繰り返しが
        すり抜けていた（実戦ログで確認）。文字2グラムの重なり率で判定する。
        しきい値0.45は実戦ログの繰り返しペア（0.51〜1.00）と
        正常な別コメント（0.31以下）の間を取って校正した値。
        """
        ga, gb = self._char_bigrams(a), self._char_bigrams(b)
        if not ga or not gb:
            return False
        overlap = len(ga & gb) / min(len(ga), len(gb))
        return overlap >= threshold

    def _is_duplicate(self, comment: str) -> bool:
        """直近のコメントと同じ・またはほぼ同じ文面かを判定する（v4.11強化）"""
        for prev in self._last_comments[-10:]:
            if comment == prev or self._is_similar(comment, prev):
                logger.info(f"[重複検出] 直近のコメントと類似のため破棄: '{comment[:30]}'")
                return True
        return False

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

    # 普通の視聴者コメントには絶対に出てこない、内部プロンプト由来の言い回し。
    # Geminiが指示文をオウム返しした場合、これらが本文に混じる。
    PROMPT_LEAK_MARKERS = [
        "【重要】",
        "20文字以上",
        "1〜2文で",
        "1文で書いて",
        "で書いてください",
        "返答してください",
        "完結した文章",
        "書き切ること",
        "この話題はそろそろ終わり",
        "テーマのヒント",
        "音声認識のため",
        "誤変換や途切れ",
        "深掘り質問",
        "【配信者の発言】",
        "【現在のゲーム画面】",
        "これまでの会話の流れ",
        "自然な友達口調",
        # v4.51: 俳句イベントで実際に漏れたAIの注釈（「※これは例であり、
        # 実際にはこの俳句を投稿しません」）を検出するマーカー
        "※これは",
        "これは例",
        "投稿しません",
    ]

    def _looks_like_prompt_leak(self, text: str) -> bool:
        """生成結果に内部プロンプト（命令文）が漏れていないかチェックする"""
        for marker in self.PROMPT_LEAK_MARKERS:
            if marker in text:
                logger.warning(f"[プロンプト漏れ検出] '{marker}' が含まれているため投稿を破棄します: {repr(text[:40])}")
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

    def is_in_topic_cooldown(self) -> bool:
        """話題クールダウン中かどうか（v4.10: lane_manager用の公開メソッド）"""
        return self._is_in_topic_cooldown()

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
                 silence_seconds: float = 0,
                 username: str = "", **_ignored) -> Optional[str]:

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
            return self._generate_speech_response(speech_text)
        elif trigger == CommentTrigger.SILENCE_BREAKER:
            return self._generate_silence_breaker(silence_seconds)
        elif trigger == CommentTrigger.UNRECOGNIZED_SPEECH:
            return None  # 聞き取り失敗コメントは出さない
        return None

    def _generate_speech_response(self, speech_text: str) -> Optional[str]:
        # 音声認識結果にNGワードが含まれていたらGeminiに送らずスキップ
        if self._contains_ng_word(speech_text):
            logger.warning(f"[入力NGワード] 音声認識結果にポリシー違反の可能性があるためスキップ")
            return None

        # ヒアリング質問への回答をプロフィールに記録
        if self._profile_manager and (time.time() - self._last_hearing_time) < 120:
            # 直前のヒアリング質問から2分以内の発言は回答として記録
            self._profile_manager.add_topic(f"好み: {speech_text[:20]}")
            logger.info(f"[ヒアリング記録] {speech_text[:30]}")

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


        # ステートに応じてプロンプトを変える
        if self._conversation_state == ConversationState.DEEPENING:
            style_hint = f"この話題はすでに{self._current_topic_turns}往復しています。深掘り質問か自分の意見を述べてください。"
        else:
            style_hint = "深掘り質問・共感・ツッコミ・自分の意見など自由なスタイルで返してください。"

        prompt = f"""{history_text}【配信者の発言】
「{speech_text}」

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

    def _get_notebook_pages(self, context_text: str, active_viewers=None) -> str:
        """手帳2.0の「関連ページ」を取得する（v4.20）。
        今の話題に関係する視聴者メモ・用語だけをプロンプトに貼るための部品。"""
        if not self._profile_manager:
            return ""
        try:
            pages = self._profile_manager.get_relevant_pages(context_text, active_viewers)
            if pages:
                return f"【手帳メモ（今の話題に関係する情報）】\n{pages}\n\n"
        except Exception as e:
            logger.debug(f"手帳ページ取得失敗: {e}")
        return ""

    def generate_context_comment(self, digest_text: str, active_viewers=None) -> Optional[str]:
        """文脈レーン用：貯まった発言をまとめて1コメント生成する（v4.10）。

        ペース管理（クールダウン）はlane_manager側が唯一の持ち主なので、
        ここではコメント間隔のチェックを行わない。
        話題クールダウン・NGワード・重複チェックは行う。
        """
        if not digest_text or not digest_text.strip():
            return None

        # NGワードチェック（Geminiに送る前に弾く）
        if self._contains_ng_word(digest_text):
            logger.warning("[入力NGワード] 文脈にポリシー違反の可能性があるためスキップ")
            return None

        # 話題クールダウン中はスキップ
        if self._is_in_topic_cooldown():
            return None

        # 会話履歴用に1行テキスト化（メモの箇条書き記号を除去）
        speech_line = digest_text.replace('・', ' ').replace('\n', ' ').strip()

        # 着地ステートの場合は締めのコメント
        if self._conversation_state == ConversationState.LANDING:
            comment = self._generate_landing_comment(speech_line)
            if comment:
                self._conversation_history.append({"role": "streamer", "content": speech_line[:120]})
                if len(self._conversation_history) > 20:
                    self._conversation_history.pop(0)
                self._record_comment(comment)
                self._conversation_state = ConversationState.COOLDOWN
                self._topic_end_time = time.time()
                self._current_topic_turns = 0
                logger.info(f"[着地] {comment} → {self._topic_cooldown}秒クールダウン開始")
                return comment
            return None

        history_text = self._build_history_text()
        pages_text = self._get_notebook_pages(digest_text + " " + history_text, active_viewers)

        # ステートに応じてヒントを変える（ステート更新は成功時のみ＝リトライ安全）
        if self._conversation_state == ConversationState.DEEPENING:
            style_hint = f"この話題はすでに{self._current_topic_turns}往復しています。深掘り質問か自分の意見を述べてください。"
        else:
            style_hint = "深掘り質問・共感・ツッコミ・自分の意見など自由なスタイルで返してください。"

        prompt = f"""{pages_text}{history_text}【配信者のここ最近の発言（音声認識・古い順）】
{digest_text}

※音声認識のため誤変換や途切れがある場合があります。文脈から意図を推測してください。
※発言の全部に触れる必要はありません。一番面白い・気になる部分をひとつ拾って反応してください。
※【最重要】上の「ここ最近の発言」を最優先で拾うこと。会話の流れはあくまで背景。
　話題が変わっていたら、前の話題を引きずらずに切り替えること。
※あなた（視聴者）が前に書いたコメントと同じ内容・似た言い回しの繰り返しは絶対禁止。
　毎回まったく新しい切り口で書くこと。
※明らかに誤変換と思われる意味不明な単語は、無理にネタにせず、意味の通る部分に反応すること。

{style_hint}
自然な日本語で1〜2文で返答してください。
【重要】必ず20文字以上の完結した文章で書くこと。「え、」「やばい、」など途中で終わることは禁止。"""

        comment = self._call_gemini(prompt)
        if comment and not self._is_duplicate(comment):
            self._conversation_history.append({"role": "streamer", "content": speech_line[:120]})
            if len(self._conversation_history) > 20:
                self._conversation_history.pop(0)
            self._update_conversation_state(speech_line)
            self._record_comment(comment)
            logger.info(f"[文脈反応] コメント生成 (ステート:{self._conversation_state.value}, {self._current_topic_turns}往復): {comment}")
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

    def _generate_silence_breaker(self, silence_seconds: float) -> Optional[str]:
        # IDLE状態に戻す
        if self._conversation_state == ConversationState.IDLE:
            self._conversation_state = ConversationState.TOPIC_RAISED
            self._current_topic_turns = 0
            self._topic_start_time = time.time()

        history_text = self._build_history_text()


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
直前に「{last_streamer_speech}」と言っていました。
この発言についてさらに深掘りする質問か、関連した話題を振るコメントを1文で書いてください。
必ず文章を最後まで書き切ること。"""
        else:
            topic = self.SILENCE_PROMPTS[self._silence_prompt_index % len(self.SILENCE_PROMPTS)]
            self._silence_prompt_index += 1
            prompt = f"""{history_text}配信者が{int(silence_seconds)}秒間無言です。
視聴者として話しかけるコメントを1文で書いてください。
テーマのヒント：「{topic}」
必ず文章を最後まで書き切ること。"""

        comment = self._call_gemini(prompt)
        if comment and not self._is_duplicate(comment):
            self._record_comment(comment)
            logger.info(f"[無言打破] {comment}")
            return comment
        return None

    def _generate_direct_conversation(self, speech_text: str) -> Optional[str]:
        ai_name = getattr(self.config, 'AI_NAME', '太郎')
        history_text = self._build_history_text()
        pages_text = self._get_notebook_pages(speech_text + " " + history_text)
        self._conversation_history.append({"role": "streamer", "content": speech_text})
        if len(self._conversation_history) > 20:
            self._conversation_history.pop(0)

        prompt = f"""{pages_text}{history_text}【配信者から{ai_name}への直接の呼びかけ】
「{speech_text}」

配信者が直接あなた（{ai_name}）に話しかけています。
自分の言葉でしっかり答えてください。
必ず日本語のみで、文章を最後まで書き切ること。"""

        comment = self._call_gemini(prompt, smart=True)  # v4.30: 会話は上位モデル
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
            comment = self._call_gemini(prompt, smart=True)  # v4.30: 視聴者対応は上位モデル
        else:
            question = content.strip() if content.strip() else '何か話して'
            history_text = self._build_history_text()
            prompt = f"""{history_text}視聴者からの質問：「{question}」
自然に答えてください。1〜3文、日本語のみ。"""
            comment = self._call_gemini(prompt, smart=True)  # v4.30: 視聴者対応は上位モデル

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
    def generate_speech_response(self, speech_text: str) -> Optional[str]:
        return self._generate_speech_response(speech_text)

    def generate_silence_breaker(self, silence_seconds: float) -> Optional[str]:
        return self._generate_silence_breaker(silence_seconds)
