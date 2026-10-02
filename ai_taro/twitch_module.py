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

        # v4.53: ギミック参加（ボット告知の単語に釣られて参加する）
        self._gimmick_pending = []  # [(投稿予定時刻, 単語)]

    def set_viewer_comment_callback(self, callback):
        """視聴者コメントを受け取った時に呼ぶコールバックを設定する"""
        self._viewer_comment_callback = callback

    def set_read_aloud_worker(self, worker):
        """v4.57: 読み上げ係（read_aloud.ReadAloudWorker）を設定する。Noneなら読み上げない"""
        self._read_aloud_worker = worker

    def read_aloud(self, username: str, content: str, emote_names=None):
        """v4.57: 読み上げ係にコメントを渡す（すぐ戻る。合成・再生は読み上げ係のスレッド）"""
        worker = getattr(self, '_read_aloud_worker', None)
        if worker is not None and content:
            worker.enqueue_comment(username, content, emote_names)

    def read_aloud_taro(self, message: str):
        """v4.57: 太郎自身の投稿を読み上げ係に渡す（Geminiの声がオンならその声で）"""
        worker = getattr(self, '_read_aloud_worker', None)
        if worker is not None and message:
            worker.enqueue_taro(message)

    @staticmethod
    def extract_emote_names(content: str, tags) -> list:
        """v4.57: Twitchのemotesタグ（例 '25:0-4,12-16/1902:6-10'）からエモート名を取り出す"""
        try:
            raw = (tags or {}).get('emotes') or ''
            names = set()
            for group in raw.split('/'):
                if ':' not in group:
                    continue
                _, positions = group.split(':', 1)
                for pos in positions.split(','):
                    start, end = pos.split('-')
                    names.add(content[int(start):int(end) + 1])
            return list(names)
        except Exception:
            return []

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

    # ------------------------------------------------------------
    # v4.53: ギミック参加
    # ボット告知（「行進」と入力すると〜等）に含まれるギミック単語を拾い、
    # 少し間を置いて「その単語だけ」を投稿して遊びに参加する。
    # AIおしゃべり反応（無視/反応ボット設定）とは別系統なので、
    # 告知ボットが除外アカウントでも動く。
    # ------------------------------------------------------------

    def check_gimmick(self, username: str, content: str) -> Optional[str]:
        """告知にギミック単語が含まれていたら、その単語を返す"""
        if not getattr(self.config, 'GIMMICK_ENABLED', False):
            return None
        if not content:
            return None
        raw = getattr(self.config, 'GIMMICK_ANNOUNCER_ACCOUNTS', 'nightbot')
        announcers = {a.strip().lower() for a in raw.split(',') if a.strip()}
        if username.lower() not in announcers:
            return None
        words = getattr(self.config, 'GIMMICK_WORDS', '')
        for word in (w.strip() for w in words.split(',')):
            if word and word in content:
                return word
        return None

    def schedule_gimmick(self, word: str):
        """ギミック単語の投稿を予約する（すぐ打つと機械的に見えるため間を置く）"""
        import random as _random
        dmin = float(getattr(self.config, 'GIMMICK_DELAY_MIN', 5))
        dmax = float(getattr(self.config, 'GIMMICK_DELAY_MAX', 30))
        delay = _random.uniform(dmin, max(dmin, dmax))
        self._gimmick_pending.append((time.time() + delay, word))
        logger.info(f"[ギミック参加] {int(delay)}秒後に「{word}」を投稿します")

    def pop_due_gimmick(self) -> Optional[str]:
        """投稿時刻になったギミック単語を1件取り出す（なければNone）"""
        now = time.time()
        for i, (due, word) in enumerate(self._gimmick_pending):
            if now >= due:
                self._gimmick_pending.pop(i)
                return word
        return None

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
                    self._stopped = False  # v4.59: 停止したら送信の係も終わる（前の太郎の係が裏で再送し続けないように）
                    # v4.59: 接続の様子を記録するための数字（2026-10-02 の切断の原因が追えなかったため）
                    self._last_recv = time.time()   # 最後にTwitchから何か届いた時刻
                    self._recv_comments = 0         # 受け取ったコメント数（状態の記録ごとに0に戻す）
                    self._last_status_log = time.time()

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
                        # v4.53: ギミック参加（除外フィルタの手前で単語だけ拾う）
                        gimmick_word = twitch_module_ref.check_gimmick(username, content)
                        if gimmick_word:
                            twitch_module_ref.schedule_gimmick(gimmick_word)
                        # v4.57: 読み上げ（除外リストの判定は読み上げ係の中で行う）
                        twitch_module_ref.read_aloud(
                            username, content,
                            twitch_module_ref.extract_emote_names(content, getattr(message, 'tags', None)))
                        # コメントを記録（除外アカウントは内部でフィルタ）
                        twitch_module_ref.record_chat_message(username)
                        self._recv_comments += 1
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

                async def event_raw_data(self, data):
                    self._last_recv = time.time()

                async def event_reconnect(self):
                    logger.info("Twitchから「つなぎ直して」の連絡があり、つなぎ直します")

                def connection_report(self) -> str:
                    """今の接続の様子を1行で（記録用）"""
                    try:
                        conn = self._connection
                        ws = conn._websocket
                        keeper = conn._keeper
                        parts = [
                            f"最後の受信={int(time.time() - self._last_recv)}秒前",
                            f"接続={'あり' if conn.is_alive else 'なし'}",
                        ]
                        if ws is not None:
                            parts.append(f"ws.closed={ws.closed}")
                            if ws.close_code is not None:
                                parts.append(f"切断コード={ws.close_code}")
                            if ws.exception() is not None:
                                parts.append(f"ws例外={ws.exception()!r}")
                            transport = getattr(getattr(ws, "_writer", None), "transport", None)
                            if transport is not None:
                                parts.append(f"送信口が閉じかけ={transport.is_closing()}")
                        if keeper is not None:
                            if keeper.done():
                                exc = None if keeper.cancelled() else keeper.exception()
                                parts.append(f"受信係=停止（{'取り消し' if keeper.cancelled() else repr(exc)}）")
                            else:
                                parts.append("受信係=動作中")
                        return " / ".join(parts)
                    except Exception as e:
                        return f"（様子を取れませんでした: {e}）"

                def _maybe_log_status(self):
                    """10分ごとに接続の様子と受け取ったコメント数を記録する"""
                    if time.time() - self._last_status_log < 600:
                        return
                    logger.info(f"[Twitch状態] 10分間のコメント受信 {self._recv_comments}件 / {self.connection_report()}")
                    self._recv_comments = 0
                    self._last_status_log = time.time()

                async def event_channel_joined(self, channel):
                    self._channel = channel
                    logger.info(f"チャンネルに参加しました: #{channel.name}")

                async def event_error(self, error: Exception, data=None):
                    import traceback
                    logger.error(f"Twitchエラー: {error!r}" + (f"（受信データ: {str(data)[:120]}）" if data else ""))
                    logger.error("".join(traceback.format_exception(type(error), error, error.__traceback__)).rstrip())

                async def _reconnect_for_send(self):
                    """送信に失敗したとき、Twitchにつなぎ直す（失敗しても次の再送でまた試す）"""
                    if self._stopped:
                        return
                    try:
                        logger.info("Twitchにつなぎ直します...")
                        await asyncio.wait_for(self._connection._connect(), timeout=20)
                        logger.info("Twitchにつなぎ直しました")
                    except Exception as e:
                        logger.warning(f"Twitchへのつなぎ直しに失敗: {e}")

                async def _message_sender(self):
                    """キューからメッセージを取り出して送信するループ。

                    v4.10: 45秒待ち（二重クールダウン）を廃止。ペース管理は
                    lane_manager側が持つため、ここは連投防止の最低間隔のみ。
                    送信失敗時はメッセージを手元に保持して再送を試みる。
                    """
                    pending = None       # 送信失敗時の再送用 (message, is_priority)
                    fail_count = 0       # 連続失敗回数
                    while not self._stopped:
                        try:
                            self._maybe_log_status()
                            # v4.53: 投稿時刻が来たギミック単語を通常キューへ流す
                            gimmick = twitch_module_ref.pop_due_gimmick()
                            if gimmick:
                                message_queue.put(gimmick)

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
                                    logger.warning(f"[Twitch状態] 送信失敗時: {self.connection_report()}")
                                    pending = (message, is_priority)
                                    self._channel = None  # 次回チャンネルを取り直す
                                    # v4.59: 「closing transport」は接続が半分切れたまま twitchio が気づかない状態
                                    # （2026-10-02 配信で13分間すべて失敗）。待つだけでは直らないので、こちらからつなぎ直す
                                    await self._reconnect_for_send()
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
                            # v4.57: 太郎の投稿も読み上げる（今のTTAも読んでいるため同じ動きにする。
                            # Twitchは自分の投稿を受信側に返さないので、送信成功時にここで渡す）
                            twitch_module_ref.read_aloud_taro(message)

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
        if self._bot:
            self._bot._stopped = True  # v4.59: 送信の係を終わらせる（再送待ちのメッセージも捨てる）
        # asyncioの未処理例外ログを抑制（切断途中の twitchio の内部エラーが画面に出ないよう、切る前に）
        if self._loop and not self._loop.is_closed():
            self._loop.set_exception_handler(lambda loop, ctx: None)
            if self._bot and self._bot._send_task:
                self._loop.call_soon_threadsafe(self._bot._send_task.cancel)
        if self._bot and self._loop:
            try:
                future = asyncio.run_coroutine_threadsafe(self._bot.close(), self._loop)
                future.result(timeout=5)
            except Exception as e:
                logger.debug(f"Twitch切断時のエラー（無視）: {e}")
        if self._loop and not self._loop.is_closed():
            # v4.59: twitchio は close() しても動き続ける（run_forever のまま）ため、ここで止める。
            # 止めないと前の太郎の送信の係が裏で再送し続けていた（2026-10-02 配信の記録で確認）
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except RuntimeError:
                pass
        if getattr(self, '_thread', None):
            self._thread.join(timeout=5)
        logger.info("Twitch連携モジュールを停止しました")

    @property
    def is_connected(self) -> bool:
        """接続状態を返す"""
        return self._thread.is_alive() if hasattr(self, '_thread') else False
