"""
レーン管理モジュール v4.10（Phase 2: 二レーン化）

配信中の入力を「返事の速さ」で2つのレーンに振り分ける。

【即時レーン】呼ばれたら数秒で返事（クールダウン無視・優先送信）
  - 「太郎、〜」の音声呼びかけ
  - !太郎 などの視聴者コマンド
  - 視聴者コメントでの名指し

【文脈レーン】捨てずに貯めて、会話の切れ目で1コメント
  - 配信者の独り言・叫び → 文脈メモに追記
  - 会話の切れ目（CONTEXT_GAP_SECONDS）＋最低間隔（COMMENT_COOLDOWN_SECONDS）
    がそろったらまとめて1コメント生成
  - 普通の視聴者コメント → 会話履歴に記録＋たまに「ちょっかい」

v4.00まで存在した「クールダウン中の発言廃棄」（実戦で廃棄率32%）を解消する。
"""

import logging
import random
import threading
import time
from typing import Callable, Optional

from comment_generator import CommentTrigger

logger = logging.getLogger(__name__)


class LaneManager:
    """入力の振り分けとコメントのペース管理を一手に引き受けるクラス。

    v4.10からコメントのペース管理（クールダウン）はこのクラスが唯一の
    持ち主になる。twitch_module側の送信待ちは連投防止（2秒）のみ。
    """

    def __init__(self, config, comment_gen, twitch, audio,
                 log_queue=None,
                 state_display: Optional[Callable] = None):
        self.config = config
        self.comment_gen = comment_gen
        self.twitch = twitch
        self.audio = audio
        self.log_queue = log_queue
        self.state_display = state_display

        # ペース管理（このクラスが唯一の持ち主）
        self._last_comment_time = 0.0

        # 文脈メモ（配信者の発言を捨てずに貯める場所）
        self._context_memo = []  # [{'text': str, 'time': float}]
        self._memo_lock = threading.Lock()
        self._next_context_attempt = 0.0  # 生成失敗時のリトライ待ち
        self._context_fail_count = 0      # 同じ材料での連続失敗回数（v4.11）

        # ちょっかい管理
        self._last_chokkai_time = 0.0

        # ボット通知への反応管理（v4.11: 同じ内容には配信中1回だけ反応）
        self._last_bot_reaction_time = 0.0
        self._seen_bot_notifications = set()

        # 最近コメントした視聴者（v4.20: 手帳の視聴者ページを引くために使う）
        self._active_viewers = {}  # {username: last_comment_time}

        # 会話継続モード（v4.30: 呼びかけ応答後しばらくは続きの発言も即応答）
        self._conversation_until = 0.0   # この時刻まで会話モード
        self._conversation_turns = 0     # 現在の会話の往復数

    # ============================================================
    # 共通部品
    # ============================================================

    def _notify_comment(self, comment: str):
        """GUIログにコメントを表示し、会話ステート表示を更新する"""
        if self.log_queue is not None:
            self.log_queue.put(f"COMMENT:{comment}")
        if self.state_display is not None:
            try:
                self.state_display()
            except Exception:
                pass

    def _send_priority(self, comment: str):
        """即時レーン: 優先キューで送信（送信待ちの列に並ばない）"""
        self.twitch.send_comment_priority(comment)
        self._last_comment_time = time.time()
        self._notify_comment(comment)

    def _send_normal(self, comment: str):
        """文脈レーン: 通常キューで送信"""
        self.twitch.send_comment(comment)
        self._last_comment_time = time.time()
        self._notify_comment(comment)

    def _cooldown_ok(self) -> bool:
        """文脈レーンの最低コメント間隔を満たしているか"""
        cooldown = getattr(self.config, 'COMMENT_COOLDOWN_SECONDS', 45)
        return (time.time() - self._last_comment_time) >= cooldown

    def _chat_is_busy(self) -> bool:
        """チャットが活発で、太郎（文脈レーン）が黙るべき状態か"""
        if not self.twitch.is_chat_active():
            return False
        quiet_seconds = getattr(self.config, 'CHAT_QUIET_RESUME_SECONDS', 30)
        last_chat = self.twitch.get_last_chat_time()
        return last_chat > 0 and (time.time() - last_chat) < quiet_seconds

    def _detect_direct_call(self, text: str) -> tuple:
        """「太郎、〜」形式の呼びかけかどうかを判定する。
        Returns: (is_direct_call: bool, question: str)
        呼び名のバリエーションすべてを文頭マッチで判定（長い名前から照合）。
        """
        ai_name = getattr(self.config, 'AI_NAME', 'AIコメント太郎')
        name_variants = sorted(
            {ai_name, 'AIコメント太郎', 'コメント太郎', '太郎'},
            key=len, reverse=True  # 「太郎」の誤爆防止のため長い名前から
        )
        for name in name_variants:
            if not text.startswith(name):
                continue
            rest = text[len(name):]
            for sep in ['、', '，', ' ', '。', '！', '？', '!', '?']:
                if rest.startswith(sep):
                    rest = rest[len(sep):]
                    break
            return True, rest.strip()
        return False, text

    # ============================================================
    # 文脈メモの操作
    # ============================================================

    def _memo_append(self, text: str):
        """発言を文脈メモに追記する。上限を超えたら古いものから消す。"""
        max_chars = getattr(self.config, 'MAX_SPEECH_CONTEXT_CHARS', 500)
        self._context_fail_count = 0  # 新しい材料が来たら失敗カウントをリセット（v4.11）
        with self._memo_lock:
            self._context_memo.append({'text': text, 'time': time.time()})
            # 合計文字数が上限を超えたら古い発言から捨てる
            while (len(self._context_memo) > 1
                   and sum(len(e['text']) for e in self._context_memo) > max_chars):
                dropped = self._context_memo.pop(0)
                logger.debug(f"[文脈メモ] 古い発言を圧縮: '{dropped['text'][:20]}...'")

    def _memo_take_all(self) -> str:
        """文脈メモの中身を時系列テキストで取り出し、メモを空にする"""
        with self._memo_lock:
            if not self._context_memo:
                return ""
            digest = "\n".join(f"・{e['text']}" for e in self._context_memo)
            self._context_memo.clear()
            return digest

    def has_pending_context(self) -> bool:
        with self._memo_lock:
            return len(self._context_memo) > 0

    # ============================================================
    # 入力の受け口（振り分け役）
    # ============================================================

    def on_speech(self, text: str):
        """音声認識結果を受け取り、レーンに振り分ける"""
        is_direct, question = self._detect_direct_call(text)
        now = time.time()

        # v4.30: 会話継続モード判定
        # 呼びかけへの応答後、一定時間内の発言は「会話の続き」として即応答する
        max_turns = getattr(self.config, 'CONVERSATION_MAX_TURNS', 3)
        in_conversation = (not is_direct
                           and now < self._conversation_until
                           and self._conversation_turns < max_turns)

        if is_direct or in_conversation:
            # 【即時レーン】クールダウン無視で即返答
            ai_name = getattr(self.config, 'AI_NAME', 'AIコメント太郎')
            if is_direct:
                self._conversation_turns = 0  # 新しい会話の始まり
                logger.info(f"[{ai_name}呼びかけ] {question or text}")
            else:
                logger.info(f"[会話モード] 続きの発言として応答: {text[:30]}")
            comment = self.comment_gen.generate(
                CommentTrigger.DIRECT_CONVERSATION,
                speech_text=question or text
            )
            if comment:
                logger.info(f"コメント送信(即時): {comment}")
                self._send_priority(comment)
                self._conversation_turns += 1
                if self._conversation_turns >= max_turns:
                    # キャッチボールはここまで。通常モードに戻る
                    self._conversation_until = 0.0
                    logger.info(f"[会話モード] {max_turns}往復で一区切り。通常モードに戻ります")
                else:
                    window = getattr(self.config, 'CONVERSATION_WINDOW_SECONDS', 30)
                    self._conversation_until = time.time() + window
            return

        # 【文脈レーン】独り言・叫び → 捨てずにメモへ
        self._memo_append(text)
        logger.info(f"[文脈メモ] 追加: '{text[:30]}'"
                    f"（現在{len(self._context_memo)}件）")

    def on_viewer_command(self, command_str: str, username: str):
        """!太郎 等の視聴者コマンド → 【即時レーン】"""
        comment = self.comment_gen.generate(
            CommentTrigger.VIEWER_COMMAND,
            speech_text=command_str,
            username=username
        )
        if comment:
            logger.info(f"[視聴者コマンド] {username} → {comment}")
            self._send_priority(comment)

    def on_viewer_comment(self, content: str, username: str, is_bot: bool = False):
        """視聴者コメント・ボット通知を受け取る。
        名指し → 即時レーン / それ以外 → 文脈材料＋ちょっかい判定
        """
        now = time.time()

        # --- ボット通知（nightbot等のお知らせ） ---
        # v4.11: 同じ内容の定期お知らせには配信中1回だけ反応する。
        # （実戦で5分ごとに同じ宣伝へ律儀に相槌を打っていたため。
        # 　通知の種類ごとに1回は反応するので「全く無反応」にはならない）
        if is_bot:
            import re as _re
            content_key = _re.sub(r'[\s　]+', '', content)[:80]
            if content_key in self._seen_bot_notifications:
                logger.debug(f"[ボット通知] 既に反応済みの内容のためスキップ: {content[:30]}")
                return
            if now - self._last_bot_reaction_time < 300:
                return
            # 太郎の直前コメントから15秒は空ける（連投見え防止・v4.11）
            if now - self._last_comment_time < 15:
                return
            prompt = (f"Twitchのボット通知：「{content}」。"
                      f"これを読んで視聴者として一言コメントしてください。日本語1文のみ。")
            comment = self.comment_gen._call_gemini(prompt)
            if comment:
                self._last_bot_reaction_time = now
                self._seen_bot_notifications.add(content_key)
                logger.info(f"[ボット通知反応] {username}: {content} → {comment}")
                self._send_normal(comment)
            return

        # --- 視聴者コメントを学習（プロフィール）＆会話履歴に記録 ---
        self._active_viewers[username] = now  # v4.20: 手帳の視聴者ページ用
        profile_mgr = getattr(self.comment_gen, '_profile_manager', None)
        if profile_mgr:
            profile_mgr.add_viewer_comment(username, content)
        if len(content) >= 4:
            self.comment_gen._conversation_history.append({
                'role': 'viewer',
                'content': f"{username}：{content}"
            })
            if len(self.comment_gen._conversation_history) > 20:
                self.comment_gen._conversation_history.pop(0)

        # --- 名指しコメント → 【即時レーン】 ---
        ai_name = getattr(self.config, 'AI_NAME', '太郎')
        if ai_name in content or 'コメント太郎' in content or '太郎' in content:
            prompt = self._build_viewer_reaction_prompt(username, content)
            comment = self.comment_gen._call_gemini(prompt, smart=True)  # v4.30: 名指しは上位モデル
            if comment:
                logger.info(f"[名指し反応] {username}: {content} → {comment}")
                self._send_priority(comment)
            return

        # --- 普通のコメント → ちょっかい判定（ランダム） ---
        if not getattr(self.config, 'VIEWER_COMMENT_REACTION_ENABLED', True):
            return
        probability = getattr(self.config, 'CHOKKAI_PROBABILITY', 0.25)
        min_interval = getattr(self.config, 'CHOKKAI_MIN_INTERVAL', 120)
        if now - self._last_chokkai_time < min_interval:
            return
        # 太郎の直前コメントから15秒は空ける（連投見え防止）
        if now - self._last_comment_time < 15:
            return
        if random.random() >= probability:
            return  # 外れ。コメントは会話履歴に残っているので文脈には活きる

        # 当たり！ちょっかいをかける
        # ※ちょっかいは「チャット活発時は黙る」ルールの対象外（設計判断：
        #   盛り上がりに太郎がたまに混ざってくる方が面白いため）
        prompt = self._build_viewer_reaction_prompt(username, content)
        comment = self.comment_gen._call_gemini(prompt)
        if comment:
            self._last_chokkai_time = now
            logger.info(f"[ちょっかい] {username}: {content} → {comment}")
            self._send_normal(comment)

    def _build_viewer_reaction_prompt(self, username: str, content: str) -> str:
        """視聴者コメントへの反応プロンプト（常連かどうかで変える）"""
        profile_mgr = getattr(self.comment_gen, '_profile_manager', None)
        viewer_count = 0
        if profile_mgr:
            viewers = profile_mgr._profile.get('known_viewers', {})
            viewer_count = viewers.get(username, {}).get('count', 0)

        # v4.20: 手帳にその人のメモがあれば添える
        notes_text = ""
        if profile_mgr:
            notes = profile_mgr._profile.get('known_viewers', {}).get(username, {}).get('notes', [])
            if notes:
                notes_text = f"（手帳メモ: この人は {'、'.join(notes[-2:])}）"

        if viewer_count >= 5:
            return (f"常連の「{username}」が「{content}」とコメントしました。{notes_text}"
                    f"親しみを込めて視聴者として自然に1文で反応してください。日本語のみ。")
        return (f"Twitchチャットに「{username}」が「{content}」と書きました。{notes_text}"
                f"視聴者として自然に1文で反応してください。日本語のみ。")

    def on_silence(self):
        """無言が続いたときの話しかけ（文脈レーンの仲間）"""
        if not self._cooldown_ok():
            return
        if self._chat_is_busy():
            return
        silence_seconds = self.audio.get_seconds_since_last_speech()
        comment = self.comment_gen.generate(
            CommentTrigger.SILENCE_BREAKER,
            silence_seconds=silence_seconds
        )
        if comment:
            logger.info(f"コメント送信(無言打破): {comment}")
            self._send_normal(comment)

    # ============================================================
    # 文脈レーンの心臓部（毎秒呼ばれる）
    # ============================================================

    def tick(self):
        """メインループから毎秒呼ばれる。会話の切れ目を検知して
        文脈メモからコメントを生成する。"""
        if not self.has_pending_context():
            return

        now = time.time()

        # 生成失敗後のリトライ待ち
        if now < self._next_context_attempt:
            return

        # 条件1: 太郎の前のコメントから最低間隔が経過していること
        if not self._cooldown_ok():
            return

        # 条件2: 会話の切れ目（最後の音声から一定秒数の無音）
        gap_seconds = getattr(self.config, 'CONTEXT_GAP_SECONDS', 7)
        if self.audio.get_seconds_since_last_speech() < gap_seconds:
            return

        # 条件3: チャットが活発なときは黙る（既存ルール維持）
        if self._chat_is_busy():
            return

        # 条件4: 話題クールダウン中は少し待つ（メモは保持したまま）
        if self.comment_gen.is_in_topic_cooldown():
            self._next_context_attempt = now + 5
            return

        # 条件5: 材料が薄すぎるときは見送って貯まるのを待つ（v4.11）
        # 「おはようございます。」1件だけ等の薄い材料だとGeminiが会話履歴に
        # 引っ張られて前のコメントを繰り返しがち（実戦ログで確認）。
        # ただし待ちすぎ防止のため、一定時間経ったら薄くても生成する。
        min_chars = getattr(self.config, 'CONTEXT_MIN_CHARS', 12)
        force_after = getattr(self.config, 'CONTEXT_FORCE_AFTER_SECONDS', 120)
        with self._memo_lock:
            total_chars = sum(len(e['text']) for e in self._context_memo)
            oldest_time = self._context_memo[0]['time'] if self._context_memo else now
        if total_chars < min_chars and (now - oldest_time) < force_after:
            return

        # 全条件クリア → メモをまとめて1コメント生成
        digest = self._memo_take_all()
        if not digest:
            return

        logger.info(f"[文脈レーン] 切れ目を検知。まとめて処理:\n{digest}")
        # v4.20: 直近10分にコメントした視聴者の手帳ページを一緒に渡す
        active = [u for u, t in self._active_viewers.items() if now - t < 600]
        comment = self.comment_gen.generate_context_comment(digest, active_viewers=active)
        if comment:
            self._context_fail_count = 0
            logger.info(f"コメント送信(文脈): {comment}")
            self._send_normal(comment)
        else:
            # 生成失敗（Gemini不調・重複など）
            self._context_fail_count += 1
            max_retry = getattr(self.config, 'CONTEXT_MAX_RETRY', 2)
            if self._context_fail_count >= max_retry:
                # v4.11: 同じ材料で失敗が続いたら潔く見送る（無限再挑戦による
                # API無駄撃ち防止。実戦で同じ独り言に6連敗した事例あり）
                logger.info(f"[文脈レーン] {self._context_fail_count}回失敗したため、この材料は見送ります")
                self._context_fail_count = 0
            else:
                logger.info("[文脈レーン] 生成失敗。30秒後に再挑戦します")
                with self._memo_lock:
                    self._context_memo.insert(0, {'text': digest.replace('・', ' ').replace('\n', ' ').strip(),
                                                  'time': now})
                self._next_context_attempt = now + 30
