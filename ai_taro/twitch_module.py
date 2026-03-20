"""
Twitch連携モジュール
TwitchのIRC（チャット）にbotアカウントとしてコメントを投稿します。
"""

import asyncio
import threading
import time
import logging
import queue
from typing import Optional

logger = logging.getLogger(__name__)


class TwitchModule:
    """
    twitchioを使ってTwitchのチャットにコメントを投稿するモジュール。
    非同期処理を別スレッドで管理します。
    """

    def __init__(self, config):
        self.config = config
        self._message_queue = queue.Queue()
        self._last_send_time = 0.0
        self._is_connected = False
        self._loop = None
        self._bot = None

        # 他視聴者コメント監視用
        self._recent_chat_times = []  # 他視聴者のコメント時刻リスト
        self._excluded_accounts = self._parse_excluded_accounts()

        # 視聴者コマンドコールバック（gui_app.pyから設定される）
        self._viewer_command_callback = None

    def set_viewer_command_callback(self, callback):
        """視聴者コマンドを受け取った時に呼ぶコールバックを設定する"""
        self._viewer_command_callback = callback

    def _parse_excluded_accounts(self) -> set:
        """configの除外アカウントリストをセットに変換"""
        raw = getattr(self.config, 'EXCLUDED_ACCOUNTS', '')
        accounts = {a.strip().lower() for a in raw.split(',') if a.strip()}
        # bot身自も除外に追加
        bot_nick = getattr(self.config, 'BOT_NICK', '').lower()
        if bot_nick:
            accounts.add(bot_nick)
        return accounts

    def record_chat_message(self, username: str):
        """他視聴者のコメントを記録する（除外アカウントは無視）"""
        if username.lower() in self._excluded_accounts:
            return
        self._recent_chat_times.append(time.time())
        # 古いエントリを削除
        window = getattr(self.config, 'CHAT_ACTIVITY_WINDOW_SECONDS', 60)
        cutoff = time.time() - window
        self._recent_chat_times = [t for t in self._recent_chat_times if t > cutoff]

    def is_chat_active(self) -> bool:
        """チャットが活発かどうか判定する"""
        if not getattr(self.config, 'CHAT_ACTIVITY_MUTE_ENABLED', True):
            return False
        window = getattr(self.config, 'CHAT_ACTIVITY_WINDOW_SECONDS', 60)
        threshold = getattr(self.config, 'CHAT_ACTIVITY_THRESHOLD', 3)
        cutoff = time.time() - window
        recent = [t for t in self._recent_chat_times if t > cutoff]
        return len(recent) >= threshold

    def get_last_chat_time(self) -> float:
        """最後に他視聴者コメントがあった時刻を返す"""
        if not self._recent_chat_times:
            return 0.0
        return max(self._recent_chat_times)

    def send_comment(self, message: str):
        """
        コメントを送信キューに追加する（スレッドセーフ）。

        Args:
            message: 送信するコメント文字列
        """
        if not message or not message.strip():
            return

        # メッセージを500文字以内に制限（Twitchの制限）
        message = message.strip()[:500]

        self._message_queue.put(message)
        logger.debug(f"コメントをキューに追加: {message}")

    def _create_bot(self):
        """twitchioのBotインスタンスを作成する"""
        try:
            import twitchio
            from twitchio.ext import commands

            config = self.config
            message_queue = self._message_queue

            class TwitchBot(commands.Bot):
                def __init__(self):
                    super().__init__(
                        token=config.BOT_TOKEN,
                        prefix="!",
                        initial_channels=[config.CHANNEL_NAME]
                    )
                    self._channel = None
                    self._send_task = None

                async def event_ready(self):
                    logger.info(f"Twitchに接続しました: {self.nick}")
                    logger.info(f"チャンネル: #{config.CHANNEL_NAME}")
                    self._channel = self.get_channel(config.CHANNEL_NAME)
                    self._send_task = asyncio.create_task(self._message_sender())

                async def event_message(self, message):
                    """他の視聴者のコメントを受信したときの処理"""
                    try:
                        if message.author is None:
                            return
                        username = message.author.name
                        content = message.content.strip() if message.content else ''
                        # 自分自身のコメントは無視
                        if username.lower() == config.BOT_NICK.lower():
                            return
                        # コメントを記録（除外アカウントは内部でフィルタ）
                        twitch_module_ref.record_chat_message(username)
                        logger.debug(f"他の視聴者コメント受信: {username}: {content}")

                        # 視聴者コマンドの処理
                        if twitch_module_ref._viewer_command_callback and getattr(config, 'VIEWER_COMMANDS_ENABLED', True):
                            ai_name = getattr(config, 'AI_NAME', '太郎')
                            cmd_prefix = getattr(config, 'VIEWER_COMMAND_PREFIX', f'!{ai_name}')

                            if content.lower() == '!hello' and getattr(config, 'COMMAND_HELLO_ENABLED', True):
                                logger.info(f"[!hello] {username}からの挨拶コマンド")
                                twitch_module_ref._viewer_command_callback(
                                    f"hello:{username}", username
                                )
                            elif content.lower() == '!status' and getattr(config, 'COMMAND_STATUS_ENABLED', True):
                                logger.info(f"[!status] {username}からのステータスコマンド")
                                twitch_module_ref._viewer_command_callback(
                                    f"status:", username
                                )
                            elif content.startswith(cmd_prefix):
                                question = content[len(cmd_prefix):].strip()
                                if question:
                                    logger.info(f"[{cmd_prefix}] {username}からの質問: {question}")
                                    twitch_module_ref._viewer_command_callback(
                                        f"ask:{question}", username
                                    )
                    except Exception as e:
                        logger.debug(f"event_messageエラー: {e}")

                async def event_channel_joined(self, channel):
                    self._channel = channel
                    logger.info(f"チャンネルに参加しました: #{channel.name}")

                async def event_error(self, error: Exception, data=None):
                    logger.error(f"Twitchエラー: {error}")

                async def _message_sender(self):
                    """キューからメッセージを取り出して送信するループ"""
                    while True:
                        try:
                            # キューからメッセージを取得（ノンブロッキング）
                            try:
                                message = message_queue.get_nowait()
                            except queue.Empty:
                                await asyncio.sleep(1)
                                continue

                            # チャンネルが取得できていない場合は待機
                            if self._channel is None:
                                self._channel = self.get_channel(config.CHANNEL_NAME)
                                if self._channel is None:
                                    logger.warning("チャンネルが見つかりません。再試行します...")
                                    message_queue.put(message)  # キューに戻す
                                    await asyncio.sleep(5)
                                    continue

                            # レートリミット: 前回送信から一定時間待機
                            elapsed = time.time() - self._last_send_time_ref[0]
                            if elapsed < config.COMMENT_COOLDOWN_SECONDS:
                                wait_time = config.COMMENT_COOLDOWN_SECONDS - elapsed
                                logger.debug(f"クールダウン中... {wait_time:.1f}秒待機")
                                await asyncio.sleep(wait_time)

                            # メッセージ送信
                            await self._channel.send(message)
                            self._last_send_time_ref[0] = time.time()
                            logger.info(f"コメント送信: {message}")

                            # 送信後の短い待機（連続送信防止）
                            await asyncio.sleep(1)

                        except Exception as e:
                            logger.error(f"メッセージ送信エラー: {e}")
                            await asyncio.sleep(5)

            # 送信時刻の共有参照（クロージャ用）
            last_send_time_ref = [0.0]

            # twitch_module_refはクロージャ内で参照できるようにする
            twitch_module_ref = self

            bot = TwitchBot()
            bot._last_send_time_ref = last_send_time_ref
            return bot

        except ImportError:
            logger.error("twitchioライブラリが見つかりません。pip install twitchio を実行してください。")
            raise

    def _run_bot(self):
        """botを非同期ループで実行する（別スレッド）"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._bot = self._create_bot()
            logger.info("Twitchに接続中...")
            self._bot.run()
        except Exception as e:
            logger.error(f"Twitch bot実行エラー: {e}")
        finally:
            self._loop.close()

    def start(self):
        """Twitch botを起動する"""
        self._thread = threading.Thread(target=self._run_bot, daemon=True)
        self._thread.start()
        logger.info("Twitch連携モジュールを起動しました")

        # 接続確立まで少し待機
        time.sleep(3)

    def stop(self):
        """Twitch botを停止する"""
        if self._bot and self._loop:
            asyncio.run_coroutine_threadsafe(self._bot.close(), self._loop)
        logger.info("Twitch連携モジュールを停止しました")

    @property
    def is_connected(self) -> bool:
        """接続状態を返す"""
        return self._thread.is_alive() if hasattr(self, '_thread') else False
