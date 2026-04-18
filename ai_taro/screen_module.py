"""
画面認識モジュール v2.07
Gemini APIのマルチモーダル機能を使って、ゲーム画面の状況をテキストで把握します。

主な改善点（v2.07）:
  - Ollama VisionモデルからGemini APIに切り替え（VRAMを使用しない）
  - 30秒に1回Gemini APIでゲーム画面を解析
  - 汎用的な状況説明をコメント生成のコンテキストに含める
  - 失敗時はスキップ（Bot停止しない）
  - config.py の SCREEN_RECOGNITION_ENABLED で有効/無効を切り替え可能
"""

import threading
import time
import logging
import base64
import io
from typing import Optional

logger = logging.getLogger(__name__)


class ScreenModule:
    """
    定期的にスクリーンショットを取得し、
    Gemini APIのマルチモーダル機能を使ってゲーム画面の状況をテキストで把握するモジュール。
    VRAMを一切使用しません。
    """

    def __init__(self, config):
        self.config = config
        self.is_running = False
        self._latest_screen_description = "配信が始まったばかりです。"
        self._description_lock = threading.Lock()
        self._last_capture_time = 0.0
        self._enabled = getattr(config, 'SCREEN_RECOGNITION_ENABLED', False)
        self._capture_interval = getattr(config, 'SCREEN_CAPTURE_INTERVAL', 300)

        # Gemini Visionモデル（遅延初期化）
        self._gemini_model = None

        # 排他制御用：comment_generatorの参照（外部からセットする）
        self.comment_generator = None

        # 画面状況が更新されたときに呼ばれるコールバック（外部からセットする）
        self.on_description_updated = None

        # 前回の画面状況（変化検知用）
        self._previous_description = ""

        # 過疎時トリガー用の外部参照（外部からセットする）
        self.audio_module = None
        self.twitch_module = None

        # ゲーム設定JSONを読み込む
        self._game_config = self._load_game_config()

    def _load_game_config(self) -> dict:
        """games/フォルダからゲーム設定JSONを読み込む"""
        import json
        import os

        game_title = getattr(self.config, 'GAME_TITLE', '')
        if not game_title:
            return {}

        # gamesフォルダのパスを探す
        base_dir = os.path.dirname(os.path.abspath(__file__))
        games_dir = os.path.join(base_dir, 'games')

        if not os.path.exists(games_dir):
            return {}

        # titleが一致するJSONを探す
        for filename in os.listdir(games_dir):
            if not filename.endswith('.json'):
                continue
            filepath = os.path.join(games_dir, filename)
            try:
                with open(filepath, encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('title') == game_title or data.get('title_en') == game_title:
                    logger.info(f"ゲーム設定を読み込みました: {filename} ({game_title})")
                    return data
            except Exception as e:
                logger.warning(f"ゲーム設定ファイルの読み込み失敗: {filename} ({e})")

        logger.info(f"ゲーム設定ファイルが見つかりません（{game_title}）。汎用プロンプトを使用します。")
        return {}

    def _get_gemini_vision_model(self):
        """Gemini Visionモデルを取得する（遅延初期化）"""
        if self._gemini_model is not None:
            return self._gemini_model

        try:
            import google.generativeai as genai

            api_key = getattr(self.config, 'GEMINI_API_KEY', '')
            if not api_key or api_key == 'your_gemini_api_key_here':
                logger.warning("Gemini APIキーが設定されていません。画面認識を無効化します。")
                return None

            genai.configure(api_key=api_key)

            model_name = getattr(self.config, 'GEMINI_MODEL', 'gemini-1.5-flash')

            self._gemini_model = genai.GenerativeModel(
                model_name=model_name,
                generation_config=genai.GenerationConfig(
                    temperature=0.3,
                    max_output_tokens=200,
                )
            )

            logger.info(f"Gemini Vision APIモデルを初期化しました: {model_name}")
            return self._gemini_model

        except ImportError:
            logger.warning(
                "google-generativeaiライブラリが見つかりません。画面認識を無効化します。\n"
                "インストール方法: pip install google-generativeai"
            )
            self._enabled = False  # ライブラリがなければ画面認識を自動無効化
            return None
        except Exception as e:
            logger.error(f"Gemini Vision APIの初期化エラー: {e}")
            return None

    def get_latest_description(self) -> str:
        """最新の画面状況テキストを返す"""
        with self._description_lock:
            return self._latest_screen_description

    def _capture_screenshot(self) -> Optional[bytes]:
        """フォートナイトのウィンドウを自動検出してキャプチャする。
        見つからない場合はプライマリモニター全体にフォールバック。
        """
        try:
            import mss
            from PIL import Image

            # フォートナイトのウィンドウを検出してその範囲をキャプチャ
            region = self._find_game_window()

            with mss.mss() as sct:
                if region:
                    logger.debug(f"フォートナイトウィンドウ検出: {region}")
                    screenshot = sct.grab(region)
                else:
                    # 見つからない場合はモニター設定にフォールバック
                    monitor_index = getattr(self.config, 'SCREEN_MONITOR_INDEX', 1)
                    if not hasattr(self, '_monitors_logged'):
                        self._monitors_logged = True
                        for i, m in enumerate(sct.monitors):
                            logger.info(f"  モニター[{i}]: {m['width']}x{m['height']} (left={m['left']}, top={m['top']})")
                        logger.info(f"フォートナイトウィンドウが見つからないため、モニター[{monitor_index}]を使用します")
                    if monitor_index >= len(sct.monitors):
                        monitor_index = 1
                    screenshot = sct.grab(sct.monitors[monitor_index])

                img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

                # 解像度を下げてトークン消費を抑える（幅1280px以下に）
                max_width = 1280
                if img.width > max_width:
                    ratio = max_width / img.width
                    new_size = (max_width, int(img.height * ratio))
                    img = img.resize(new_size, Image.LANCZOS)

                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                return buf.getvalue()

        except ImportError as e:
            logger.warning(f"画面キャプチャに必要なライブラリが見つかりません: {e}")
            logger.warning("pip install mss Pillow を実行してください。")
            return None
        except Exception as e:
            logger.warning(f"スクリーンショット取得エラー: {e}")
            return None

    def _find_game_window(self) -> Optional[dict]:
        """Windowsのウィンドウ一覧からゲームウィンドウを探して座標を返す。
        GAME_TITLE設定のキーワードでウィンドウを検索する。
        見つからない場合は None を返す。
        """
        try:
            import ctypes
            import ctypes.wintypes

            game_title = getattr(self.config, 'GAME_TITLE', 'フォートナイト')

            # JSONのwindow_titlesを優先、なければtitle_mapにフォールバック
            if self._game_config.get('window_titles'):
                target_titles = self._game_config['window_titles']
            else:
                title_map = {
                    'フォートナイト': ['Fortnite', 'FortniteClient'],
                    'Apex Legends': ['Apex Legends'],
                    'ヴァロラント': ['VALORANT'],
                    'マインクラフト': ['Minecraft'],
                    'Apex': ['Apex Legends'],
                    'VALORANT': ['VALORANT'],
                }
                target_titles = title_map.get(game_title, [game_title])

            found = {}

            def enum_windows_callback(hwnd, _):
                if not ctypes.windll.user32.IsWindowVisible(hwnd):
                    return True
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length == 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                for t in target_titles:
                    if t.lower() in title.lower():
                        rect = ctypes.wintypes.RECT()
                        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                        w = rect.right - rect.left
                        h = rect.bottom - rect.top
                        if w > 100 and h > 100:
                            found['region'] = {
                                "left": rect.left,
                                "top": rect.top,
                                "width": w,
                                "height": h
                            }
                            found['title'] = title
                            logger.info(f"ゲームウィンドウ検出: '{title}' ({w}x{h} at {rect.left},{rect.top})")
                return True

            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
            ctypes.windll.user32.EnumWindows(EnumWindowsProc(enum_windows_callback), 0)

            if not found:
                logger.debug(f"ゲームウィンドウが見つかりません（検索キー: {target_titles}）")

            return found.get('region', None)

        except Exception as e:
            logger.debug(f"ウィンドウ検出エラー（フォールバックします）: {e}")
            return None

    def _analyze_screenshot(self, image_bytes: bytes) -> Optional[str]:
        """Gemini APIのマルチモーダル機能でスクリーンショットを解析する"""
        try:
            model = self._get_gemini_vision_model()
            if model is None:
                logger.warning("[画面認識] Gemini Visionモデルの初期化失敗。APIキーを確認してください。")
                return None

            import google.generativeai as genai

            logger.info("[画面認識] Gemini API にリクエスト送信中...")

            game_title = getattr(self.config, 'GAME_TITLE', '') or 'ゲーム配信'
            gc = self._game_config  # ゲーム設定JSON

            # UI説明セクション（JSONまたはconfig.pyから）
            ui_desc = gc.get('ui_description') or getattr(self.config, 'GAME_UI_DESCRIPTION', '')
            ui_section = f"\n【{game_title}のUI説明】\n{ui_desc}\n" if ui_desc else ""

            # スキップシーンリスト（JSONまたはデフォルト）
            skip_scenes = gc.get('skip_scenes') or [
                "ロード中・ロード画面（真っ黒・真っ白・プログレスバーのみ）",
                "ロビー・マッチング待ち・試合開始前の待機画面",
                "観戦中・スペクテイター画面",
                "設定画面・オプション画面",
                "試合終了後のリザルト画面",
            ]
            skip_lines = "\n".join(f"- {s}" for s in skip_scenes)

            # マップ画面の特別処理（JSONで定義されている場合）
            map_logic = gc.get('special_logic', {}).get('map_screen', {})
            map_section = ""
            if map_logic.get('enabled') and map_logic.get('description'):
                map_section = f"\n【マップ画面（全体マップ表示）の場合】\n{map_logic['description']}\n"

            # コメントヒント（JSONで定義されている場合）
            hints = gc.get('comment_hints', [])
            hint_section = ("\n【コメントのヒント】\n" + "\n".join(f"- {h}" for h in hints) + "\n") if hints else ""

            prompt = f"""これはゲーム配信（{game_title}）のスクリーンショットです。

まず、以下のいずれかに該当する画面かどうか判断してください。
該当する場合は「スキップ:（理由）」とだけ答えてください。
{skip_lines}
{map_section}
上記に該当しない場合のみ、現在の状況を日本語で2〜3文で説明してください。{ui_section}{hint_section}
【注目してほしい情報】
- 今何のゲームをプレイしているか（画面から判断）
- プレイヤーの現在の状況（HP・点数・スコア・残り時間など見えるもの）
- 今何が起きているか（戦闘中・移動中・ターン待ち・上がり・ミッション中など）
- 画面に表示されているテキスト情報（役名・ミッション名・スコアなど）

必ず日本語のみで答えてください。"""

            response = model.generate_content([
                prompt,
                {"mime_type": "image/jpeg", "data": base64.b64encode(image_bytes).decode("utf-8")}
            ])

            if response and response.text:
                description = response.text.strip()

                # スキップ判定
                if description.startswith("スキップ"):
                    logger.info(f"[画面認識] スキップ判定: {description}")
                    return None

                logger.info(f"画面状況: {description[:100]}...")
                return description

            # レスポンスが空の場合は詳細をログに出す
            if response:
                logger.warning(f"[画面認識] Geminiからレスポンスが返りましたがテキストが空です。finish_reason: {getattr(response, 'prompt_feedback', '不明')}")
            else:
                logger.warning("[画面認識] Geminiからレスポンスなし（None）")
            return None

        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "quota" in error_str.lower():
                logger.warning("Gemini APIのレート制限に達しました。画面認識をスキップします。")
            else:
                logger.warning(f"画面解析エラー（スキップ）: {e}")
            return None

    def _is_sparse_condition(self) -> bool:
        """過疎条件を満たしているか確認する（全条件が揃ったときのみTrue）"""
        now = time.time()
        silence_threshold = getattr(self.config, 'SCREEN_SPARSE_SILENCE', 180)
        chat_threshold = getattr(self.config, 'SCREEN_SPARSE_CHAT', 180)
        comment_threshold = getattr(self.config, 'SCREEN_SPARSE_COMMENT', 120)

        # 音声認識なしの確認
        if self.audio_module:
            speech_silence = self.audio_module.get_seconds_since_last_speech()
            if speech_silence < silence_threshold:
                return False

        # チャットなしの確認
        if self.twitch_module:
            last_chat = self.twitch_module.get_last_chat_time()
            if last_chat > 0 and (now - last_chat) < chat_threshold:
                return False

        # コメント太郎の最後の発言からの時間確認
        if self.comment_generator:
            last_comment = self.comment_generator._last_comment_time
            if last_comment > 0 and (now - last_comment) < comment_threshold:
                return False

        return True

    def _screen_capture_loop(self):
        """画面キャプチャのメインループ（過疎時トリガー方式）"""
        logger.info("画面認識モジュールを起動しました（過疎時トリガー方式）")

        while self.is_running:
            current_time = time.time()

            # 過疎条件が揃ったときだけキャプチャ
            if self._is_sparse_condition() and current_time - self._last_capture_time >= self._capture_interval:
                # 排他制御：コメント生成中はスキップ
                if self.comment_generator is not None and self.comment_generator.is_generating:
                    logger.info("コメント生成中のため画面認識のAPI呼び出しをスキップします")
                    time.sleep(5)
                    continue

                logger.info("スクリーンショットを取得中...")

                try:
                    image_bytes = self._capture_screenshot()

                    if image_bytes:
                        logger.info(f"スクリーンショット取得成功 ({len(image_bytes)//1024}KB)、Gemini APIで解析中...")
                        description = self._analyze_screenshot(image_bytes)
                        if description:
                            with self._description_lock:
                                prev = self._previous_description
                                self._previous_description = self._latest_screen_description
                                self._latest_screen_description = description
                            logger.info(f"[画面認識成功] {description[:120]}")
                            # コールバックで main.py に通知
                            if self.on_description_updated:
                                try:
                                    self.on_description_updated(description)
                                except Exception as cb_err:
                                    logger.debug(f"画面コールバックエラー: {cb_err}")
                        else:
                            logger.warning("画面解析結果なし（APIエラーまたは空レスポンス）")
                    else:
                        logger.warning("スクリーンショット取得失敗（mss/Pillowのエラーを確認してください）")

                except Exception as e:
                    # エラーが発生してもBotを停止しない
                    logger.warning(f"画面認識エラー（スキップ）: {e}")

                self._last_capture_time = current_time

            time.sleep(5)  # 5秒ごとにチェック

        logger.info("画面認識モジュールを停止しました")

    def capture_now(self) -> Optional[str]:
        """即座にスクリーンショットを取得して解析する（手動呼び出し用）"""
        try:
            image_bytes = self._capture_screenshot()
            if image_bytes:
                description = self._analyze_screenshot(image_bytes)
                if description:
                    with self._description_lock:
                        self._latest_screen_description = description
                    return description
        except Exception as e:
            logger.warning(f"手動画面キャプチャエラー: {e}")
        return None

    def start(self):
        """画面認識を開始する"""
        if not self._enabled:
            logger.info("画面認識は無効化されています（config.py の SCREEN_RECOGNITION_ENABLED = False）")
            return

        # 起動前にライブラリの存在を確認する（ループ内でエラーを出さないため）
        try:
            import google.generativeai  # noqa: F401
        except ImportError:
            logger.warning(
                "google-generativeaiライブラリが見つかりません。画面認識を無効化します。\n"
                "インストール方法: pip install google-generativeai"
            )
            self._enabled = False
            return

        self.is_running = True
        self._thread = threading.Thread(target=self._screen_capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """画面認識を停止する"""
        self.is_running = False
        if hasattr(self, '_thread'):
            self._thread.join(timeout=5)
        logger.info("画面認識モジュールを停止しました")
