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

        # 取材モード（v4.50: 呼び名が未設定の視聴者に太郎が質問する）
        self._interview = None           # 進行中の取材 {'username','display','until'}
        self._interviewed = set()        # この配信で質問済みの人
        self._interview_count = 0

        # 「覚えて」コマンド（v4.50: 直前に覚えた内容。取り消し用）
        self._last_memory = None         # (kind, key, prev_value)

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
            # v4.41: 「太郎は〜」「太郎さ〜」等の助詞にも対応（v4.42: 「の」追加）
            for sep in ['、', '，', ' ', '。', '！', '？', '!', '?', 'は', 'さ', 'ね', 'の']:
                if rest.startswith(sep):
                    rest = rest[len(sep):]
                    break
            return True, rest.lstrip('、，。 ').strip()
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
            q = question or text
            if is_direct:
                self._conversation_turns = 0  # 新しい会話の始まり
                logger.info(f"[{ai_name}呼びかけ] {q}")
            else:
                logger.info(f"[会話モード] 続きの発言として応答: {text[:30]}")

            # v4.50: 特殊コマンドの判定（取り消し → 覚えて → 検索 の順）
            forget_triggers = [t for t in getattr(self.config, 'FORGET_TRIGGERS',
                               '登録しないで,覚えないで,取り消して').split(',') if t]
            if any(t in q for t in forget_triggers):
                self._handle_forget()
                return
            remember_triggers = [t for t in getattr(self.config, 'REMEMBER_TRIGGERS',
                                 '覚えて,覚えた？,覚えた?,メモして,記録して').split(',') if t]
            if any(t in q for t in remember_triggers):
                self._handle_remember()
                return

            search_triggers = [t for t in getattr(self.config, 'SEARCH_TRIGGERS',
                               '調べて,検索して,ググって,について教えて').split(',') if t]
            use_search = (getattr(self.config, 'SEARCH_ENABLED', True)
                          and any(t in q for t in search_triggers))

            if use_search:
                logger.info(f"[検索] {q}")
                comment = self.comment_gen.generate_search_answer(q)
                if not comment:
                    logger.info("[検索] 失敗したため通常会話で返答します")
                    comment = self.comment_gen.generate(
                        CommentTrigger.DIRECT_CONVERSATION, speech_text=q)
            else:
                comment = self.comment_gen.generate(
                    CommentTrigger.DIRECT_CONVERSATION, speech_text=q)

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

        # v4.50: 取材の回答待ち中なら、配信者の発言を答えとして解釈してみる
        if self._interview and now < self._interview['until']:
            if self._handle_interview_answer(text, from_viewer=False):
                return  # 答えとして処理できた

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

    def on_viewer_comment(self, content: str, username: str, is_bot: bool = False,
                          display_name: str = ""):
        """視聴者コメント・ボット通知を受け取る。
        名指し → 即時レーン / それ以外 → 文脈材料＋ちょっかい判定
        v4.50: 取材の回答受付・取材の開始判定・検索トリガーも担当
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
            profile_mgr.add_viewer_comment(username, content, display_name)

        # --- v4.50: 取材の回答待ち中なら、本人のコメントを答えとして最優先 ---
        if (self._interview and now < self._interview['until']
                and username == self._interview['username']):
            if self._handle_interview_answer(content, from_viewer=True):
                return
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
            # v4.50: 視聴者からの「調べて」も検索で答える
            search_triggers = [t for t in getattr(self.config, 'SEARCH_TRIGGERS',
                               '調べて,検索して,ググって,について教えて').split(',') if t]
            if (getattr(self.config, 'SEARCH_ENABLED', True)
                    and any(t in content for t in search_triggers)):
                logger.info(f"[検索] 視聴者{username}から: {content}")
                comment = self.comment_gen.generate_search_answer(content)
                if comment:
                    self._send_priority(comment)
                    return
            prompt = self._build_viewer_reaction_prompt(username, content)
            # v4.50: MENTION_USE_SMART=False でLiteに切替可能（節約実験用）
            use_smart = getattr(self.config, 'MENTION_USE_SMART', True)
            comment = self.comment_gen._call_gemini(prompt, smart=use_smart)
            if comment:
                logger.info(f"[名指し反応] {username}: {content} → {comment}")
                self._send_priority(comment)
            return

        # --- v4.50: 呼び名が未設定なら取材のチャンス ---
        if self._maybe_start_interview(username):
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
        """視聴者コメントへの反応プロンプト（常連かどうかで変える）。
        v4.43: ユーザーIDではなく呼び名で呼ぶ。初見さんには「はじめまして」。"""
        profile_mgr = getattr(self.comment_gen, '_profile_manager', None)
        viewer_count = 0
        display = username
        if profile_mgr:
            viewers = profile_mgr._profile.get('known_viewers', {})
            viewer_count = viewers.get(username, {}).get('count', 0)
            try:
                display = profile_mgr._display_name(username)
            except Exception:
                pass
        has_name = (display != username)

        # 呼び方のルール（IDの連呼を防ぐ）
        if has_name:
            name_rule = f"※この人を呼ぶときは必ず「{display}」と呼ぶこと。ユーザーIDでは呼ばないこと。"
        else:
            name_rule = "※ユーザーID（英数字の羅列）をそのまま呼び名にしないこと。名前は出さずに自然に返すこと。"

        # v4.20: 手帳にその人のメモがあれば添える
        notes_text = ""
        if profile_mgr:
            notes = profile_mgr._profile.get('known_viewers', {}).get(username, {}).get('notes', [])
            if notes:
                notes_text = f"（手帳メモ: この人は {'、'.join(notes[-2:])}）"

        if viewer_count >= 5:
            return (f"常連の「{display}」が「{content}」とコメントしました。{notes_text}{name_rule}"
                    f"親しみを込めて視聴者として自然に1文で反応してください。日本語のみ。")
        if viewer_count <= 1:
            return (f"初めて見る視聴者（ID: {username}）が「{content}」とコメントしました。{name_rule}"
                    f"「はじめまして」の気持ちを込めて、視聴者として温かく1文で反応してください。日本語のみ。")
        return (f"Twitchチャットで「{display}」が「{content}」と書きました。{notes_text}{name_rule}"
                f"視聴者として自然に1文で反応してください。日本語のみ。")

    # ============================================================
    # 取材モード（v4.50: 太郎が呼び名を質問して手帳を埋める）
    # ============================================================

    def _maybe_start_interview(self, username: str) -> bool:
        """呼び名が未設定の視聴者に取材（質問）を始める。始めたらTrue"""
        if not getattr(self.config, 'INTERVIEW_ENABLED', True):
            return False
        max_per_stream = getattr(self.config, 'INTERVIEW_MAX_PER_STREAM', 3)
        if (self._interview is not None
                or self._interview_count >= max_per_stream
                or username in self._interviewed):
            return False
        profile_mgr = getattr(self.comment_gen, '_profile_manager', None)
        if not profile_mgr:
            return False
        val = profile_mgr._profile.get('viewer_names', {}).get(username)
        if val != "未設定":
            return False  # 設定済み、またはNone（無視リスト）は取材しない
        # 太郎の直前コメントから15秒は空ける
        if time.time() - self._last_comment_time < 15:
            return False

        display = profile_mgr._display_name(username)
        question = f"{display}さん、こんにちは！ところで、なんてお呼びすればいいですか？"
        self._send_normal(question)  # 定型文なのでAPI消費ゼロ
        window = getattr(self.config, 'INTERVIEW_ANSWER_WINDOW', 120)
        self._interview = {'username': username, 'display': display,
                           'until': time.time() + window}
        self._interviewed.add(username)
        self._interview_count += 1
        logger.info(f"[取材] {display}（{username}）さんに呼び名を質問しました")
        return True

    def _handle_interview_answer(self, content: str, from_viewer: bool) -> bool:
        """取材への答えを解釈して呼び名を保存する。処理できたらTrue"""
        iv = self._interview
        who = "本人" if from_viewer else "配信者"
        prompt = (f"視聴者「{iv['display']}」に「なんてお呼びすればいいですか？」と質問しました。\n"
                  f"{who}の返事:「{content}」\n"
                  f"この返事に呼び名（呼んでほしい名前）が含まれていれば、呼び名だけを出力してください。\n"
                  f"含まれていない・関係ない話なら「なし」とだけ出力してください。")
        raw = self.comment_gen._call_gemini_raw(prompt)
        name = (raw or "").strip().strip('「」『』"\'。、 ')
        if (not name or name == "なし" or len(name) > 15
                or self.comment_gen._contains_ng_word(name)):
            return False  # 答えではなかった（会話は通常処理に流す）

        profile_mgr = getattr(self.comment_gen, '_profile_manager', None)
        prev = profile_mgr.set_viewer_name(iv['username'], name) if profile_mgr else "未設定"
        self._last_memory = ('viewer_name', iv['username'], prev)
        self._interview = None
        confirm = f"{name}さんですね、覚えました！これからよろしくね！"
        logger.info(f"[取材完了] {iv['username']} = {name}")
        self._send_priority(confirm)
        return True

    # ============================================================
    # 「覚えて」コマンド（v4.50: 声で手帳に書き込む）
    # ============================================================

    @staticmethod
    def _parse_json(raw: str):
        """AIの出力からJSONを取り出す（```json フェンス等を除去）"""
        import json as _json
        if not raw:
            return None
        text = raw.strip().replace('```json', '').replace('```', '').strip()
        try:
            return _json.loads(text)
        except Exception:
            return None

    def _handle_remember(self):
        """直近の会話から記憶すべきことを抽出して手帳に即時保存する"""
        history = self.comment_gen._conversation_history[-8:]
        if not history:
            self._send_priority("ごめん、覚えるための会話がまだないみたいだ。もう一回教えてくれる？")
            return
        hist_text = "\n".join(
            f"{'配信者' if m.get('role') == 'streamer' else ('視聴者' if m.get('role') == 'viewer' else '太郎')}: {m.get('content', '')}"
            for m in history
        )
        prompt = f"""以下はTwitch配信の直近の会話です。
{hist_text}

配信者がAIの太郎に「今のを覚えて」と指示しました。会話から記憶すべき情報を1つ選び、次のいずれかの形のJSONだけを出力してください（説明や前置きは不要）:
{{"kind": "correction", "wrong": "誤変換された語", "right": "正しい語", "confirm": "覚えたよ！で始まる確認の一言"}}
{{"kind": "glossary", "term": "用語", "desc": "意味", "confirm": "同上"}}
{{"kind": "status", "text": "近況の内容", "confirm": "同上"}}
{{"kind": "joke", "text": "定番ネタ・合言葉", "confirm": "同上"}}
{{"kind": "none"}}

kindの種類:
- "correction": 音声認識の誤変換の訂正。**配信者が「XじゃなくてY」「Xは間違い、正しくはY」のように、誤りと正解をはっきり言い直している場合だけ**使うこと。
  似た言葉が2つ出てきただけの場合や、どちらが正しいか会話から断定できない場合は correction にしないこと
- "glossary": 用語・固有名詞の意味
- "status": 配信者の近況
- "joke": 定番ネタ・合言葉
- "none": 何を覚えるべきか不明

間違った訂正を覚えると音声認識が壊れるため、correction にするか迷ったら "none" か他の種類を選ぶこと。
confirmには「覚えたよ！」で始めて、何をどう覚えたかを具体的に短く言うこと。"""

        raw = self.comment_gen._call_gemini_raw(prompt)
        data = self._parse_json(raw)
        profile_mgr = getattr(self.comment_gen, '_profile_manager', None)

        if not data or not profile_mgr or data.get('kind') in (None, 'none'):
            self._send_priority("ごめん、何を覚えればいいか分からなかった。もう一回教えてくれる？")
            return

        kind = data.get('kind')
        saved = False
        if kind == 'correction' and data.get('wrong') and data.get('right'):
            prev = profile_mgr.get_corrections().get(data['wrong'])
            profile_mgr.add_correction(data['wrong'], data['right'])
            self._last_memory = ('correction', data['wrong'], prev)
            # 訂正はその場で耳にも反映（次の発言から効く）
            try:
                self.audio.set_corrections(profile_mgr.get_corrections())
            except Exception:
                pass
            saved = True
        elif kind == 'glossary' and data.get('term'):
            prev = profile_mgr._profile.get('glossary', {}).get(data['term'])
            profile_mgr.add_glossary_term(data['term'], data.get('desc', ''))
            self._last_memory = ('glossary', data['term'], prev)
            saved = True
        elif kind == 'status' and data.get('text'):
            profile_mgr.add_streamer_status(data['text'])
            self._last_memory = ('status', data['text'], None)
            saved = True
        elif kind == 'joke' and data.get('text'):
            profile_mgr.add_joke(data['text'])
            self._last_memory = ('joke', data['text'], None)
            saved = True

        if not saved:
            self._send_priority("ごめん、何を覚えればいいか分からなかった。もう一回教えてくれる？")
            return

        profile_mgr.save()
        confirm = (data.get('confirm') or "").strip()
        if (not confirm or len(confirm) > 100
                or self.comment_gen._contains_ng_word(confirm)):
            confirm = "覚えたよ！手帳にメモしておいた！"
        logger.info(f"[覚えて] {kind}として記録: {self._last_memory[1]}")
        self._send_priority(confirm)

    def _handle_forget(self):
        """直前に覚えた内容を取り消す"""
        profile_mgr = getattr(self.comment_gen, '_profile_manager', None)
        if not self._last_memory or not profile_mgr:
            self._send_priority("あれ、取り消すものが見当たらないよ？")
            return
        kind, key, prev = self._last_memory
        if kind == 'viewer_name':
            profile_mgr._profile.setdefault('viewer_names', {})[key] = prev or "未設定"
        elif kind == 'correction':
            corrections = profile_mgr._profile.setdefault('corrections', {})
            if prev:
                corrections[key] = prev
            else:
                corrections.pop(key, None)
            try:
                self.audio.set_corrections(profile_mgr.get_corrections())
            except Exception:
                pass
        elif kind == 'glossary':
            glossary = profile_mgr._profile.setdefault('glossary', {})
            if prev:
                glossary[key] = prev
            else:
                glossary.pop(key, None)
        elif kind == 'status':
            profile_mgr._profile['streamer_status'] = [
                s for s in profile_mgr._profile.get('streamer_status', [])
                if s.get('text') != key]
        elif kind == 'joke':
            profile_mgr._profile['jokes'] = [
                j for j in profile_mgr._profile.get('jokes', []) if j != key]
        profile_mgr.save()
        logger.info(f"[取り消し] {kind}: {key}")
        self._last_memory = None
        self._send_priority("了解、さっきのは取り消しておいたよ！")

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
        now = time.time()

        # v4.50: 取材の回答待ちタイムアウト（静かに引っ込める）
        if self._interview and now > self._interview['until']:
            logger.info(f"[取材] {self._interview['display']}さんからの回答なし。今回は見送ります")
            self._interview = None

        if not self.has_pending_context():
            return

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
