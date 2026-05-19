"""
AIコメント太郎 v3.53 - メインコントローラー
配信中に音声を認識し、自然な日本語コメントを自動投稿するbotです。
音声認識: Google Web Speech API（高精度・無料）
コメント生成: Gemini API（gemini-1.5-flash・無料枠あり）
画面認識: Gemini APIマルチモーダル（VRAMを使用しない）
"""

import time
import logging
import signal
import sys
import threading
from typing import Optional

import config as cfg
from audio_module import AudioModule
from comment_generator import CommentGenerator, CommentTrigger
from twitch_module import TwitchModule
from screen_module import ScreenModule


def setup_logging():
    """ロギングの設定。起動のたびにログファイルを上書き（1配信分のみ保持）"""
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    log_level = getattr(logging, cfg.LOG_LEVEL, logging.INFO)

    handlers = [logging.StreamHandler(sys.stdout)]
    if cfg.LOG_FILE:
        # mode='w' で起動のたびに上書き（前回のログは消える）
        handlers.append(logging.FileHandler(cfg.LOG_FILE, encoding="utf-8", mode='w'))

    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=handlers
    )


logger = logging.getLogger(__name__)


class TwitchAIBot:
    """
    AIコメント太郎 v3.53のメインコントローラー。
    各モジュールを統合し、タイミング制御を行います。
    """

    def __init__(self):
        self.audio = AudioModule(cfg)
        self.generator = CommentGenerator(cfg)
        self.twitch = TwitchModule(cfg)
        self.screen = ScreenModule(cfg)

        # 排他制御のため screen_module に comment_generator の参照をセット
        self.screen.comment_generator = self.generator

        # 過疎時トリガー用の参照をセット
        self.screen.audio_module = self.audio
        self.screen.twitch_module = self.twitch

        # 音声認識の前段階フィルター用にconfigをセット
        self.audio.set_config(cfg)

        # 視聴者コメント反応コールバック
        self._last_viewer_reaction_time = 0.0

        def _on_viewer_comment(content: str, username: str, is_bot: bool = False):
            now = __import__('time').time()
            cooldown = getattr(cfg, 'VIEWER_COMMENT_REACTION_COOLDOWN', 120)
            if now - self._last_viewer_reaction_time < cooldown:
                return
            if not self._can_send_comment():
                return
            trigger_type = "bot_notification" if is_bot else "viewer_comment"
            prompt_text = f"視聴者「{username}」がチャットに「{content}」と書きました。これに対して自然に反応してください。1文で。必ず日本語のみで文章を最後まで書き切ること。"
            comment = self.generator._call_gemini(prompt_text)
            if comment:
                self._send_comment(comment)
                self._last_viewer_reaction_time = now
                logger.info(f"[{trigger_type}反応] {username}: {content} → {comment}")

        self.twitch.set_viewer_comment_callback(_on_viewer_comment)

        # 画面認識結果が更新されたときにコメントを生成するコールバックをセット
        def _on_screen_updated(description: str):
            if not self._can_send_comment():
                return
            comment = self.generator.generate(
                CommentTrigger.SCREEN_EVENT,
                screen_situation=description
            )
            if comment:
                self._send_comment(comment)
        self.screen.on_description_updated = _on_screen_updated

        self._is_running = False
        self._last_comment_time = 0.0
        self._pending_speech = []
        self._speech_lock = threading.Lock()
        self._speech_timer: Optional[threading.Timer] = None
        self._unrecognized_count = 0

    def _on_speech_detected(self, text: str):
        """
        音声認識でテキストが検出されたときのコールバック。
        一定時間後にコメントを生成する（話し終わりを待つ）。
        """
        logger.debug(f"発話検知: {text}")

        with self._speech_lock:
            self._pending_speech.append(text)
            self._unrecognized_count = 0  # 認識成功したらリセット

        # 既存のタイマーをキャンセルして新しいタイマーをセット
        if self._speech_timer is not None:
            self._speech_timer.cancel()

        self._speech_timer = threading.Timer(
            12.0,  # 12秒後にバッファを処理（断片をまとめる）
            self._process_pending_speech
        )
        self._speech_timer.daemon = True
        self._speech_timer.start()

    def _process_pending_speech(self):
        """
        蓄積された発話テキストをもとにコメントを生成・送信する。
        """
        with self._speech_lock:
            if not self._pending_speech:
                return
            speech_text = " ".join(self._pending_speech)
            self._pending_speech = []

        # クールダウンチェック
        if not self._can_send_comment():
            logger.debug("クールダウン中のためコメントをスキップ")
            return

        # 画面状況を取得
        screen_situation = self.screen.get_latest_description()

        # コメント生成
        comment = self.generator.generate(
            CommentTrigger.SPEECH_RESPONSE,
            speech_text=speech_text,
            screen_situation=screen_situation
        )

        if comment:
            self._send_comment(comment)

    def _on_unrecognized_speech(self, consecutive: int = 1):
        """
        音声が聞き取れなかったときのコールバック。
        短時間に連続して聞き取れなかった場合にのみコメントを生成する。
        """
        threshold = getattr(self.config, 'UNRECOGNIZED_THRESHOLD', 6)
        if consecutive >= threshold:
            self._unrecognized_count = 0
            if not self._can_send_comment():
                return
            comment = self.generator.generate(
                CommentTrigger.UNRECOGNIZED_SPEECH,
                screen_situation=""
            )
            if comment:
                self._send_comment(comment)

    def _on_silence_detected(self):
        """
        無言状態が続いたときのコールバック。
        話しかけるコメントを生成する。
        """
        if not self._can_send_comment():
            return

        silence_seconds = self.audio.get_seconds_since_last_speech()
        screen_situation = self.screen.get_latest_description()

        comment = self.generator.generate(
            CommentTrigger.SILENCE_BREAKER,
            screen_situation=screen_situation,
            silence_seconds=silence_seconds
        )

        if comment:
            self._send_comment(comment)

    def _can_send_comment(self) -> bool:
        """コメントを送信できる状態かチェックする"""
        elapsed = time.time() - self._last_comment_time
        if elapsed < cfg.COMMENT_COOLDOWN_SECONDS:
            return False
        # チャットが活発なときは黙る
        if self.twitch.is_chat_active():
            quiet_seconds = getattr(cfg, 'CHAT_QUIET_RESUME_SECONDS', 30)
            last_chat = self.twitch.get_last_chat_time()
            if last_chat > 0 and (time.time() - last_chat) < quiet_seconds:
                return False
        return True

    def _send_comment(self, comment: str):
        """コメントをTwitchに送信する"""
        self.twitch.send_comment(comment)
        self._last_comment_time = time.time()
        logger.info(f"コメント送信: {comment}")

    def start(self):
        """botを起動する"""
        logger.info("=" * 60)
        logger.info("AIコメント太郎 v3.53 を起動します")
        logger.info(f"チャンネル: #{cfg.CHANNEL_NAME}")
        logger.info(f"音声認識: Google Web Speech API（日本語）")
        logger.info(f"コメント生成: Gemini API ({cfg.GEMINI_MODEL})")
        logger.info(f"発言フィルター: {cfg.SPEECH_MIN_LENGTH}文字以下を無視")
        logger.info(f"会話ステート: 最大{cfg.CONVERSATION_MAX_TURNS}往復 / {cfg.TOPIC_COOLDOWN_SECONDS}秒クールダウン")
        screen_status = "有効" if cfg.SCREEN_RECOGNITION_ENABLED else "無効"
        logger.info(f"画面認識: {screen_status}")
        logger.info("=" * 60)

        self._is_running = True

        # コールバックの設定
        self.audio.set_speech_callback(self._on_speech_detected)
        self.audio.set_silence_callback(self._on_silence_detected)
        self.audio.set_unrecognized_callback(self._on_unrecognized_speech)

        # 各モジュールの起動
        logger.info("Twitch接続を開始します...")
        self.twitch.start()
        time.sleep(3)

        # 配信情報をTwitch APIから取得
        stream_info = self.twitch.get_stream_info()
        if stream_info:
            self.generator.set_stream_info(stream_info)
            if stream_info.get('game_name'):
                self.screen._stream_game_name = stream_info['game_name']
                logger.info(f"📡 配信情報取得成功 - ゲーム: {stream_info['game_name']}")
            if stream_info.get('title'):
                logger.info(f"📡 配信タイトル: {stream_info['title']}")
        else:
            logger.info("📡 配信情報取得なし（オフラインまたはAPI未設定）")

        logger.info("ゲーム画面認識を開始します...")
        self.screen.start()

        logger.info("音声認識を開始します（Google Web Speech API）...")
        self.audio.start()

        logger.info("bot が稼働中です。Ctrl+C で停止します。")

    def stop(self):
        """botを停止する"""
        logger.info("bot を停止します...")
        self._is_running = False

        if self._speech_timer:
            self._speech_timer.cancel()

        self.audio.stop()
        self.screen.stop()
        self.twitch.stop()

        logger.info("bot を停止しました。")

    def run(self):
        """botを起動してメインループを実行する"""
        self.start()

        try:
            # メインスレッドはシグナル待機
            while self._is_running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Ctrl+C が押されました。停止します...")
        finally:
            self.stop()


def main():
    """エントリーポイント"""
    setup_logging()

    # 設定チェック
    if cfg.BOT_TOKEN == "oauth:your_oauth_token_here":
        logger.error("config.py の BOT_TOKEN が設定されていません。")
        logger.error("config.py を編集して、Twitch OAuthトークンを設定してください。")
        sys.exit(1)

    if cfg.BOT_NICK == "your_bot_account_name":
        logger.error("config.py の BOT_NICK が設定されていません。")
        logger.error("config.py を編集して、botアカウントのユーザー名を設定してください。")
        sys.exit(1)

    if cfg.CHANNEL_NAME == "your_channel_name":
        logger.error("config.py の CHANNEL_NAME が設定されていません。")
        logger.error("config.py を編集して、配信チャンネル名を設定してください。")
        sys.exit(1)

    if cfg.GEMINI_API_KEY == "your_gemini_api_key_here":
        logger.error("config.py の GEMINI_API_KEY が設定されていません。")
        logger.error("https://aistudio.google.com/app/apikey でAPIキーを取得してください。")
        sys.exit(1)

    bot = TwitchAIBot()

    # シグナルハンドラの設定
    def signal_handler(signum, frame):
        bot.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    bot.run()


if __name__ == "__main__":
    main()
