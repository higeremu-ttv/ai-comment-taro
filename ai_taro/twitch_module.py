"""
Twitch連携モジュール v4.10
TwitchのIRC（チャット）にbotアカウントとしてコメントを投稿します。

v4.10の変更:
- 送信側の45秒待ち（二重クールダウン）を廃止し、連投防止（2秒）のみに変更。
  コメントのペース管理は lane_manager が唯一の持ち主。
- 送信失敗時にメッセージを失わず、時間を置いて再送するように変更
  （配信オフライン時の「Cannot write to closing transport」対策）
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
        self._priority_queue = queue.Queue()  # 謎かけ等の優先送信キュー
        self._last_send_time = 0.0
        self._is_connected = False
        self._loop = None
        self._bot = None

        # 他視聴者コメント監視用
        self._recent_chat_times = []  # 他視聴者のコメント時刻リスト
        self._excluded_accounts = self._parse_excluded_accounts()

        # 視聴者コマンドコールバック（gui_app.pyから設定される）
        self._viewer_command_callback = None

        # 視聴者コメント反応コールバック
        self._viewer_comment_callback = None

        # 反応するボットアカウント
        self._reaction_bot_accounts = self._parse_reaction_bot_accounts()

    def set_viewer_comment_callback(self, callback):
        """視聴者コメントを受け取った時に呼ぶコールバックを設定する"""
        self._viewer_comment_callback = callback

    def _parse_reaction_bot_accounts(self) -> set:
        """反応するボットアカウントリストをセットに変換"""
        raw = getattr(self.config, 'REACTION_BOT_ACCOUNTS', '')
        return {a.strip().lower() for a in raw.split(',') if a.strip()}

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

    def get_stream_info(self) -> dict:
        """
        Twitch APIから配信情報（タイトル・ゲーム名）を取得する。
        Client ID + Client Secret でApp Access Tokenを取得して使用。
        失敗した場合は空dictを返す。
        """
        try:
            import urllib.request
            import urllib.parse
            import json

            channel_name = getattr(self.config, 'CHANNEL_NAME', '')
            client_id = getattr(self.config, 'TWITCH_CLIENT_ID', '')
            client_secret = getattr(self.config, 'TWITCH_CLIENT_SECRET', '')

            if not channel_name or not client_id or not client_secret:
                logger.info("📡 配信情報取得スキップ（Client IDまたはClient Secret未設定）")
                return {}

            # Step1: App Access Tokenを取得
            token_url = "https://id.twitch.tv/oauth2/token"
            token_data = urllib.parse.urlencode({
                'client_id': client_id,
                'client_secret': client_secret,
                'grant_type': 'client_credentials'
            }).encode('utf-8')
            token_req = urllib.request.Request(token_url, data=token_data)
            with urllib.request.urlopen(token_req, timeout=5) as r:
                token_info = json.loads(r.read().decode())
            access_token = token_info.get('access_token', '')
            if not access_token:
                logger.info("📡 App Access Token取得失敗")
                return {}

            # Step2: 配信情報を取得
            url = f"https://api.twitch.tv/helix/streams?user_login={channel_name}"
            req = urllib.request.Request(url)
            req.add_header('Authorization', f'Bearer {access_token}')
            req.add_header('Client-Id', client_id)
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode())

            streams = data.get('data', [])
            if not streams:
                logger.info("📡 配信情報取得なし（オフライン）")
                return {}

            stream = streams[0]
            info = {
                'title': stream.get('title', ''),
                'game_name': stream.get('game_name', ''),
            }
            logger.info(f"📡 配信情報取得成功 - ゲーム: {info['game_name']} / タイトル: {info['title']}")
            return info

        except Exception as e:
            logger.info(f"📡 配信情報取得失敗: {e}")
            return {}

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

    def send_comment_priority(self, message: str):
        """
        コメントを優先送信キューに追加する（謎かけなど時間的タイミングが重要なもの用）。

        Args:
            message: 送信するコメント文字列
        """
        if not message or not message.strip():
            return
        message = message.strip()[:500]
        self._priority_queue.put(message)
        logger.debug(f"優先コメントをキューに追加: {message}")

    def _create_bot(self):
        """twitchioのBotインスタンスを作成する"""
        try:
            import twitchio
            from twitchio.ext import commands

            config = self.config
            message_queue = self._message_queue
            priority_queue = self._priority_queue

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
                        # v4.50: Twitch表示名（例: 桃煌ぺてぃる）も取得する
                        try:
                            display_name = message.author.display_name or ''
                        except Exception:
                            display_name = ''
                        # 自分自身のコメントは無視
                        if username.lower() == config.BOT_NICK.lower():
                            return
                        # コメントを記録（除外アカウントは内部でフィルタ）
                        twitch_module_ref.record_chat_message(username)
                        logger.debug(f"他の視聴者コメント受信: {username}: {content}")

                        # ボット通知への反応（nightbot等のお知らせ系）
                        if twitch_module_ref._viewer_comment_callback:
                            username_lower = username.lower()
                            reaction_bots = twitch_module_ref._reaction_bot_accounts
                            excluded = twitch_module_ref._excluded_accounts

                            if username_lower in reaction_bots:
                                # 反応ボットのお知らせに反応
                                logger.info(f"[ボット通知] {username}: {content}")
                                twitch_module_ref._viewer_comment_callback(
                                    content, username, is_bot=True, display_name=display_name)
                            elif username_lower not in excluded and content and len(content) >= 4:
                                # 通常視聴者コメントへの反応
                                if getattr(config, 'VIEWER_COMMENT_REACTION_ENABLED', True):
                                    twitch_module_ref._viewer_comment_callback(
                                        content, username, is_bot=False, display_name=display_name)

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
                    """キューからメッセージを取り出して送信するループ。

                    v4.10: 45秒待ち（二重クールダウン）を廃止。ペース管理は
                    lane_manager側が持つため、ここは連投防止の最低間隔のみ。
                    送信失敗時はメッセージを手元に保持して再送を試みる。
                    """
                    pending = None       # 送信失敗時の再送用 (message, is_priority)
                    fail_count = 0       # 連続失敗回数
                    while True:
                        try:
                            if pending is not None:
                                message, is_priority = pending
                            else:
                                # 優先キューを先にチェック（呼びかけ・謎かけ等）
                                message = None
                                is_priority = False
                                try:
                                    message = priority_queue.get_nowait()
                                    is_priority = True
                                except queue.Empty:
                                    pass

                                # 優先キューが空なら通常キューをチェック
                                if message is None:
                                    try:
                                        message = message_queue.get_nowait()
                                    except queue.Empty:
                                        await asyncio.sleep(1)
                                        continue

                            # チャンネルが取得できていない場合は待機（メッセージは保持）
                            if self._channel is None:
                                self._channel = self.get_channel(config.CHANNEL_NAME)
                                if self._channel is None:
                                    logger.warning("チャンネルが見つかりません。再試行します...")
                                    pending = (message, is_priority)
                                    await asyncio.sleep(5)
                                    continue

                            # 連投防止: 前回送信から最低間隔だけ空ける（優先キューはスキップ）
                            if not is_priority:
                                min_interval = getattr(config, 'SEND_MIN_INTERVAL_SECONDS', 2)
                                elapsed = time.time() - self._last_send_time_ref[0]
                                if elapsed < min_interval:
                                    await asyncio.sleep(min_interval - elapsed)

                            # メッセージ送信
                            try:
                                await self._channel.send(message)
                            except Exception as e:
                                # 送信失敗（配信オフライン・接続切れ等）。
                                # メッセージを失わず、時間を置いて再送する
                                fail_count += 1
                                if fail_count <= 5:
                                    wait = min(10 * fail_count, 60)
                                    logger.warning(
                                        f"送信失敗（{fail_count}回目）: {e} "
                                        f"→ {wait}秒後に再送します。Twitch接続が切れている可能性があります"
                                    )
                                    pending = (message, is_priority)
                                    self._channel = None  # 次回チャンネルを取り直す
                                    await asyncio.sleep(wait)
                                else:
                                    logger.error(f"送信を{fail_count}回失敗したため、このメッセージは破棄します: {message}")
                                    pending = None
                                    fail_count = 0
                                    await asyncio.sleep(10)
                                continue

                            # 送信成功
                            pending = None
                            fail_count = 0
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
            try:
                future = asyncio.run_coroutine_threadsafe(self._bot.close(), self._loop)
                future.result(timeout=5)
            except Exception as e:
                logger.debug(f"Twitch切断時のエラー（無視）: {e}")
        # asyncioの未処理例外ログを抑制
        if self._loop and not self._loop.is_closed():
            self._loop.set_exception_handler(lambda loop, ctx: None)
        logger.info("Twitch連携モジュールを停止しました")

    @property
    def is_connected(self) -> bool:
        """接続状態を返す"""
        return self._thread.is_alive() if hasattr(self, '_thread') else False
