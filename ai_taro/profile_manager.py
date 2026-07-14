"""
プロフィール管理モジュール v4.20（手帳2.0）
配信ごとに学習した情報を蓄積し、次回起動時に読み込む。

【手帳2.0の構造】
- 常時貼る「基本ページ」: 配信者の名前・別名・呼び名対応・常連・最近の話題・近況・定番ネタ
- 話題に応じて貼る「関連ページ」: 視聴者ごとのメモ / 固有名詞辞書（該当語が出たときだけ）
- 固有名詞辞書はWhisperの認識ヒントにも自動で流れる（誤認対策）

旧形式（v1）の learned_profile.json は読み込み時に自動で新形式へ引き継ぐ。
"""

import json
import os
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

PROFILE_FILE = "learned_profile.json"
MAX_TOPICS = 60          # 保持する話題の最大数（v4.20: 20→60に拡大）
MAX_JOKES = 15           # 定番ネタの最大数
MAX_STATUS = 10          # 配信者の近況の最大数
MAX_GLOSSARY = 60        # 固有名詞辞書の最大数
MAX_CORRECTIONS = 40     # 訂正辞書（誤変換→正しい語）の最大数
MAX_VIEWER_NOTES = 5     # 視聴者1人あたりのメモ最大数
BASE_MAX_CHARS = 500     # 常時貼る基本ページの上限文字数
PAGES_MAX_CHARS = 600    # 関連ページの上限文字数
WHISPER_TERMS_MAX = 20   # Whisperヒントに流す語数の上限


class ProfileManager:

    def __init__(self, base_dir: str = ""):
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.profile_path = os.path.join(self.base_dir, PROFILE_FILE)
        self._profile = self._load()

    # ============================================================
    # 読み込み・保存・旧形式からの引き継ぎ
    # ============================================================

    def _default_profile(self) -> dict:
        return {
            "version": 2,
            "streamer_name": "",
            "streamer_aliases": [],
            "streamer_info": {},
            "streamer_status": [],   # [{"text": "...", "date": "2026-07-11"}]
            "known_friends": [],
            "viewer_names": {},
            "known_viewers": {},     # {username: {count, samples, notes, last_seen}}
            "glossary": {},          # {"固有名詞": "ひとこと説明"}
            "corrections": {},       # {"誤変換された語": "正しい語"}（v4.43）
            "jokes": [],             # ["定番ネタ・合言葉"]
            "recent_topics": [],
            "last_updated": ""
        }

    def _migrate_v1(self, data: dict) -> dict:
        """旧形式（v1）の手帳を新形式（v2）へ引き継ぐ。既存データは一切消さない。"""
        base = self._default_profile()
        base.update(data)  # 既存の値で上書き
        base["version"] = 2
        # 視聴者エントリに新しい欄（メモ・最終来訪日）を追加
        for username, v in base.get("known_viewers", {}).items():
            if isinstance(v, dict):
                v.setdefault("notes", [])
                v.setdefault("last_seen", "")
        logger.info("手帳を新形式（手帳2.0）に引き継ぎました")
        return base

    def _load(self) -> dict:
        """プロフィールファイルを読み込む"""
        if not os.path.exists(self.profile_path):
            return self._default_profile()
        try:
            with open(self.profile_path, encoding='utf-8') as f:
                data = json.load(f)
            if data.get("version") != 2:
                data = self._migrate_v1(data)
            logger.info(
                f"学習済みプロフィールを読み込みました"
                f"（話題数: {len(data.get('recent_topics', []))} / "
                f"辞書: {len(data.get('glossary', {}))}語 / "
                f"視聴者: {len(data.get('known_viewers', {}))}人）"
            )
            return data
        except Exception as e:
            logger.warning(f"プロフィール読み込み失敗: {e}")
            return self._default_profile()

    def save(self):
        """プロフィールをファイルに保存する"""
        try:
            self._profile['last_updated'] = time.strftime('%Y-%m-%d %H:%M')
            with open(self.profile_path, 'w', encoding='utf-8') as f:
                json.dump(self._profile, f, ensure_ascii=False, indent=2)
            logger.info("プロフィールを保存しました")
        except Exception as e:
            logger.warning(f"プロフィール保存失敗: {e}")

    # ============================================================
    # 記録系（手帳への書き込み）
    # ============================================================

    def add_topic(self, topic: str):
        """話題を追加する（重複・古いものは削除）"""
        if not topic or len(topic) < 5:
            return
        topics = self._profile.setdefault('recent_topics', [])
        if topic not in topics:
            topics.append(topic)
        if len(topics) > MAX_TOPICS:
            self._profile['recent_topics'] = topics[-MAX_TOPICS:]

    def add_friend(self, name: str):
        """仲間の名前を追加する"""
        if not name:
            return
        friends = self._profile.setdefault('known_friends', [])
        if name not in friends:
            friends.append(name)
            logger.info(f"仲間を追加: {name}")

    def set_streamer_name(self, name: str):
        """配信者名をプロフィールに設定する"""
        if name and not self._profile.get('streamer_name'):
            self._profile['streamer_name'] = name

    def add_streamer_info(self, key: str, value: str):
        """配信者の情報を追加する（ヒアリング結果）"""
        if not key or not value or len(value) < 2:
            return
        info = self._profile.setdefault('streamer_info', {})
        info[key] = value[:50]
        logger.info(f"配信者情報を記録: {key} = {value[:20]}")
        self.save()

    def add_streamer_status(self, text: str):
        """配信者の近況を追加する（v4.20）例：「最近腰痛」「新PC検討中」"""
        if not text or len(text) < 3:
            return
        status_list = self._profile.setdefault('streamer_status', [])
        # 同じ内容があれば日付だけ更新（先に古い方を消す）
        status_list[:] = [s for s in status_list if s.get('text') != text]
        status_list.append({'text': text[:60], 'date': time.strftime('%m/%d')})
        if len(status_list) > MAX_STATUS:
            self._profile['streamer_status'] = status_list[-MAX_STATUS:]
        logger.info(f"[手帳] 近況を記録: {text[:30]}")

    def add_glossary_term(self, term: str, description: str = ""):
        """固有名詞辞書に語を追加する（v4.20）。Whisperヒントにも自動反映される"""
        if not term or len(term) < 2 or len(term) > 20:
            return
        glossary = self._profile.setdefault('glossary', {})
        if term not in glossary:
            logger.info(f"[手帳] 用語を記録: {term} = {description[:20]}")
        glossary[term] = (description or "")[:40]
        # 上限を超えたら古いものから削除（dictは挿入順を保持）
        while len(glossary) > MAX_GLOSSARY:
            oldest = next(iter(glossary))
            del glossary[oldest]

    def add_correction(self, wrong: str, right: str):
        """訂正辞書に「誤変換→正しい語」のペアを追加する（v4.43）。
        次回配信から音声認識の結果に自動適用され、同じ誤変換が直る。"""
        if not wrong or not right:
            return
        wrong, right = wrong.strip(), right.strip()
        if wrong == right or len(wrong) < 2 or len(wrong) > 20 or len(right) > 20:
            return
        corrections = self._profile.setdefault('corrections', {})
        if corrections.get(wrong) != right:
            logger.info(f"[手帳] 訂正を記録: 「{wrong}」→「{right}」")
        corrections[wrong] = right
        while len(corrections) > MAX_CORRECTIONS:
            oldest = next(iter(corrections))
            del corrections[oldest]

    def get_corrections(self) -> dict:
        """訂正辞書を返す（v4.43・音声認識の補正用）"""
        return dict(self._profile.get('corrections', {}))

    def add_joke(self, joke: str):
        """お決まりネタ・内輪ノリを追加する（v4.20）"""
        if not joke or len(joke) < 3:
            return
        jokes = self._profile.setdefault('jokes', [])
        if joke[:60] not in jokes:
            jokes.append(joke[:60])
            logger.info(f"[手帳] 定番ネタを記録: {joke[:30]}")
        if len(jokes) > MAX_JOKES:
            self._profile['jokes'] = jokes[-MAX_JOKES:]

    def add_viewer_note(self, username: str, note: str):
        """視聴者ごとのメモを追加する（v4.20）例：「PS派」「車好き」"""
        if not username or not note or len(note) < 2:
            return
        viewers = self._profile.setdefault('known_viewers', {})
        if username not in viewers:
            viewers[username] = {'count': 0, 'samples': [], 'notes': [], 'last_seen': ''}
        notes = viewers[username].setdefault('notes', [])
        if note[:40] not in notes:
            notes.append(note[:40])
            logger.info(f"[手帳] {username}のメモを記録: {note[:25]}")
        if len(notes) > MAX_VIEWER_NOTES:
            viewers[username]['notes'] = notes[-MAX_VIEWER_NOTES:]

    def add_viewer_comment(self, username: str, content: str, display_name: str = ""):
        """視聴者のコメントを記録する（v4.50: Twitch表示名も保存）"""
        if not username or not content or len(content) < 2:
            return
        viewers = self._profile.setdefault('known_viewers', {})
        if username not in viewers:
            viewers[username] = {'count': 0, 'samples': [], 'notes': [], 'last_seen': ''}
        viewers[username].setdefault('notes', [])
        viewers[username]['count'] += 1
        viewers[username]['last_seen'] = time.strftime('%Y-%m-%d')
        if display_name and display_name != username:
            viewers[username]['display'] = display_name
        samples = viewers[username].setdefault('samples', [])
        if content not in samples:
            samples.append(content[:30])
            if len(samples) > 3:
                samples.pop(0)

        # viewer_names に未登録なら「未設定」として追加
        viewer_names = self._profile.setdefault('viewer_names', {})
        if username not in viewer_names:
            viewer_names[username] = "未設定"
            logger.info(f"[視聴者登録] {username} を未設定として追加しました")
        # 常連（10回以上）は known_friends にも追加（登録済みの呼び名で）
        if viewers[username]['count'] >= 10:
            display = viewer_names.get(username)
            if display is None or display == "未設定":
                return
            name = display[0] if isinstance(display, list) else display
            self.add_friend(name)

    # ============================================================
    # 参照系（手帳からの読み出し）
    # ============================================================

    def _display_name(self, username: str) -> str:
        """ユーザー名から呼び方を引く（v4.50: 手帳の呼び名→Twitch表示名→ID）"""
        display = self._profile.get('viewer_names', {}).get(username)
        if display is not None and display != "未設定":
            return display[0] if isinstance(display, list) else display
        # Twitch表示名（例: petil_momokira → 桃煌ぺてぃる）
        twitch_display = self._profile.get('known_viewers', {}).get(username, {}).get('display')
        if twitch_display:
            return twitch_display
        return username

    def set_viewer_name(self, username: str, name: str) -> str:
        """視聴者の呼び名を設定する（v4.50・取材の結果を保存）。
        Returns: 変更前の値（取り消し用）"""
        viewer_names = self._profile.setdefault('viewer_names', {})
        prev = viewer_names.get(username, "未設定")
        viewer_names[username] = name
        logger.info(f"[手帳] 呼び名を記録: {username} = {name}")
        self.save()
        return prev

    def get_prompt_text(self) -> str:
        """常時貼る「基本ページ」を返す（システムプロンプト用・安定情報のみ）"""
        if not self._profile:
            return ""

        lines = []

        aliases = self._profile.get('streamer_aliases', [])
        streamer_name = self._profile.get('streamer_name', '')
        if aliases and streamer_name:
            lines.append(f"- 配信者の別名: {', '.join(aliases)}（全て{streamer_name}と同一人物）")

        streamer_info = self._profile.get('streamer_info', {})
        if streamer_info:
            info_text = '、'.join([f"{k}:{v}" for k, v in list(streamer_info.items())[-5:]])
            lines.append(f"- 配信者について知っていること: {info_text}")

        # 配信者の近況（v4.20・日付つき。古い情報での知ったかぶり防止）
        status_list = self._profile.get('streamer_status', [])
        if status_list:
            status_text = '、'.join([f"{s['text']}({s['date']})" for s in status_list[-3:]])
            lines.append(f"- 配信者の近況: {status_text}（日付が古い情報は変わっているかも。断定しないこと）")

        friends = self._profile.get('known_friends', [])
        if friends:
            lines.append(f"- よく一緒にプレイする仲間: {', '.join(friends[-10:])}")

        viewer_names = self._profile.get('viewer_names', {})
        if viewer_names:
            name_list = []
            for username, display in viewer_names.items():
                if display is None or display == "未設定":
                    continue
                if isinstance(display, list):
                    name_list.append(f"{username}={'/'.join(display)}")
                else:
                    name_list.append(f"{username}={display}")
            if name_list:
                lines.append(f"- 視聴者の呼び名: {', '.join(name_list)}")

        viewer_ctx = self.get_viewer_context()
        if viewer_ctx:
            lines.append(viewer_ctx)

        # 定番ネタ（v4.20・直近3つ）
        jokes = self._profile.get('jokes', [])
        if jokes:
            lines.append(f"- この配信の定番ネタ: {', '.join(jokes[-3:])}")

        topics = self._profile.get('recent_topics', [])
        if topics:
            lines.append(f"- 最近の話題: {', '.join(topics[-8:])}")

        if not lines:
            return ""

        text = "【過去の配信から学んだこと】\n" + "\n".join(lines)
        if len(text) > BASE_MAX_CHARS:
            text = text[:BASE_MAX_CHARS] + "..."
        return text

    def get_relevant_pages(self, context_text: str, usernames=None) -> str:
        """今の話題に関係する「関連ページ」だけを返す（v4.20・毎回のプロンプト用）。
        - 視聴者ページ: 最近コメントした人のメモ
        - 固有名詞辞書: 会話に登場した語の説明
        """
        if not self._profile:
            return ""
        context_text = context_text or ""
        parts = []

        # 視聴者ページ（来ている人のぶんだけ）
        viewers = self._profile.get('known_viewers', {})
        for username in (usernames or []):
            v = viewers.get(username)
            if not v:
                continue
            notes = v.get('notes', [])
            if notes:
                parts.append(f"- {self._display_name(username)}のこと: {'、'.join(notes[-3:])}")

        # 固有名詞辞書（会話に出てきた語だけ）
        glossary = self._profile.get('glossary', {})
        for term, desc in glossary.items():
            if term and term in context_text:
                parts.append(f"- 用語「{term}」: {desc}" if desc else f"- 「{term}」は既知の固有名詞")

        if not parts:
            return ""
        text = "\n".join(parts)
        if len(text) > PAGES_MAX_CHARS:
            text = text[:PAGES_MAX_CHARS] + "..."
        return text

    def get_whisper_terms(self) -> list:
        """Whisperの認識ヒントに流す語のリストを返す（v4.20）。
        固有名詞辞書＋設定済みの呼び名＋最近の仲間から作る。"""
        terms = []
        terms.extend(list(self._profile.get('glossary', {}).keys()))
        # 訂正辞書の「正しい語」も耳のヒントに（そもそも誤認しにくくする・v4.43）
        terms.extend(list(self._profile.get('corrections', {}).values()))
        for username, display in self._profile.get('viewer_names', {}).items():
            if display is None or display == "未設定":
                continue
            if isinstance(display, list):
                terms.extend(display)
            else:
                terms.append(display)
        terms.extend(self._profile.get('known_friends', [])[-10:])
        # 重複除去（順序保持）して上限まで
        seen = set()
        unique = []
        for t in terms:
            if t and t not in seen:
                seen.add(t)
                unique.append(t)
        return unique[:WHISPER_TERMS_MAX]

    def get_viewer_context(self) -> str:
        """視聴者情報をプロンプト用テキストで返す"""
        viewers = self._profile.get('known_viewers', {})
        if not viewers:
            return ""
        top = sorted(viewers.items(), key=lambda x: x[1].get('count', 0), reverse=True)[:5]
        names = [f"{u}({d.get('count', 0)}回)" for u, d in top]
        return f"- よく来る視聴者: {', '.join(names)}"

    # ============================================================
    # 配信終了時の学習（Geminiによる抽出）
    # ============================================================

    def update_from_conversation(self, conversation_history: list, gemini_api_key: str = ""):
        """会話履歴からプロフィールを更新する（配信終了時に呼ぶ）"""
        if not conversation_history:
            return

        if gemini_api_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=gemini_api_key)
                model = genai.GenerativeModel("gemini-2.5-flash-lite")

                history_text = "\n".join([
                    f"{'配信者' if m.get('role') == 'streamer' else ('視聴者' if m.get('role') == 'viewer' else 'AI')}: {m.get('content', '')}"
                    for m in conversation_history[-20:]
                ])

                prompt = f"""以下は配信中の会話履歴です。
この会話から以下の情報をJSON形式で抽出してください：
- friends: 一緒にプレイしている仲間のニックネームのリスト（一般名詞は除く）
- topics: 主要な話題のリスト（5〜20文字で簡潔に）
- viewer_notes: 視聴者について分かったこと。{{"ユーザー名": "一言メモ"}} 形式（例: {{"turbo35gtr": "PS配信派"}}）
- glossary: 会話に出たゲーム用語・固有名詞。{{"語": "ひとこと説明"}} 形式（一般的な言葉は除く）
- corrections: 音声認識の誤変換が訂正された箇所。{{"誤変換された語": "正しい語"}} 形式
  （例: 「5変換じゃなくて誤変換だよ」という発言があれば {{"5変換": "誤変換"}}。
  　訂正の発言が明確にあった場合のみ。推測では入れないこと）
- jokes: この配信の定番ネタ・お決まりの言い回しのリスト
- streamer_status: 配信者本人の近況（体調・予定・買い物など）のリスト

会話履歴:
{history_text}

JSONのみ出力。分からない項目は空のリスト・空のオブジェクトでよい。"""

                response = model.generate_content(prompt)
                if response and response.text:
                    text = response.text.strip().replace('```json', '').replace('```', '').strip()
                    data = json.loads(text)
                    for friend in data.get('friends', []) or []:
                        self.add_friend(friend)
                    for topic in data.get('topics', []) or []:
                        self.add_topic(topic)
                    for username, note in (data.get('viewer_notes', {}) or {}).items():
                        self.add_viewer_note(username, note)
                    for term, desc in (data.get('glossary', {}) or {}).items():
                        self.add_glossary_term(term, desc)
                    for wrong, right in (data.get('corrections', {}) or {}).items():
                        self.add_correction(wrong, right)
                    for joke in data.get('jokes', []) or []:
                        self.add_joke(joke)
                    for status in data.get('streamer_status', []) or []:
                        self.add_streamer_status(status)
                    logger.info(
                        f"Geminiで手帳を更新: 仲間{len(data.get('friends', []) or [])}人・"
                        f"話題{len(data.get('topics', []) or [])}件・"
                        f"用語{len(data.get('glossary', {}) or {})}語・"
                        f"メモ{len(data.get('viewer_notes', {}) or {})}人分"
                    )
            except Exception as e:
                logger.debug(f"Geminiでのプロフィール抽出失敗（正規表現にフォールバック）: {e}")
                self._update_from_conversation_regex(conversation_history)
        else:
            self._update_from_conversation_regex(conversation_history)

        self.save()
        logger.info("会話履歴からプロフィールを更新しました")

    def _update_from_conversation_regex(self, conversation_history: list):
        """正規表現で仲間の名前を抽出（フォールバック用）"""
        import re
        for msg in conversation_history:
            content = msg.get('content', '')
            words = re.findall(r'[ぁ-んァ-ヶー一-龥a-zA-Z]{2,8}(?:さん|くん|ちゃん)', content)
            for word in words:
                if len(word) >= 3:
                    self.add_friend(word)
