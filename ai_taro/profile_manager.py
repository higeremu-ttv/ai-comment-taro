"""
プロフィール管理モジュール
配信ごとに学習した情報を蓄積し、次回起動時に読み込む。
"""

import json
import os
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

PROFILE_FILE = "learned_profile.json"
MAX_TOPICS = 20      # 保持する話題の最大数
MAX_TOKENS = 400     # プロフィールの最大トークン数（概算文字数）


class ProfileManager:

    def __init__(self, base_dir: str = ""):
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.profile_path = os.path.join(self.base_dir, PROFILE_FILE)
        self._profile = self._load()

    def _load(self) -> dict:
        """プロフィールファイルを読み込む"""
        if not os.path.exists(self.profile_path):
            return {
                "streamer_name": "",
                "known_friends": [],
                "recent_topics": [],
                "last_updated": ""
            }
        try:
            with open(self.profile_path, encoding='utf-8') as f:
                data = json.load(f)
            logger.info(f"学習済みプロフィールを読み込みました（話題数: {len(data.get('recent_topics', []))}）")
            return data
        except Exception as e:
            logger.warning(f"プロフィール読み込み失敗: {e}")
            return {}

    def save(self):
        """プロフィールをファイルに保存する"""
        try:
            self._profile['last_updated'] = time.strftime('%Y-%m-%d %H:%M')
            with open(self.profile_path, 'w', encoding='utf-8') as f:
                json.dump(self._profile, f, ensure_ascii=False, indent=2)
            logger.info("プロフィールを保存しました")
        except Exception as e:
            logger.warning(f"プロフィール保存失敗: {e}")

    def add_topic(self, topic: str):
        """話題を追加する（重複・古いものは削除）"""
        if not topic or len(topic) < 5:
            return
        topics = self._profile.setdefault('recent_topics', [])
        if topic not in topics:
            topics.append(topic)
        # 最大数を超えたら古いものを削除
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

    def get_prompt_text(self) -> str:
        """プロンプトに追加する学習済み情報テキストを返す"""
        if not self._profile:
            return ""

        lines = []

        friends = self._profile.get('known_friends', [])
        if friends:
            lines.append(f"- よく一緒にプレイする仲間: {', '.join(friends[-10:])}")

        topics = self._profile.get('recent_topics', [])
        if topics:
            lines.append(f"- 最近の話題: {', '.join(topics[-8:])}")

        if not lines:
            return ""

        text = "【過去の配信から学んだこと】\n" + "\n".join(lines)

        # トークン超過防止（概算）
        if len(text) > MAX_TOKENS:
            text = text[:MAX_TOKENS] + "..."

        return text

    def update_from_conversation(self, conversation_history: list, gemini_api_key: str = ""):
        """会話履歴からプロフィールを更新する（配信終了時に呼ぶ）"""
        if not conversation_history:
            return

        # Gemini APIで仲間の名前・話題を抽出
        if gemini_api_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=gemini_api_key)
                model = genai.GenerativeModel("gemini-2.5-flash-lite")

                # 会話履歴をテキストに変換
                history_text = "\n".join([
                    f"{'配信者' if m.get('role') == 'streamer' else 'AI'}: {m.get('content', '')}"
                    for m in conversation_history[-20:]
                ])

                prompt = f"""以下は配信中の会話履歴です。
この会話から以下の情報をJSON形式で抽出してください：
- friends: 一緒にプレイしている仲間のニックネーム・名前のリスト（一般名詞は除く）
- topics: 会話で出てきた主要な話題のリスト（5文字以内で簡潔に）

会話履歴:
{history_text}

JSONのみ出力してください。例: {{"friends": ["あおちゃん", "beeteekee"], "topics": ["フォートナイト", "2位"]}}"""

                response = model.generate_content(prompt)
                if response and response.text:
                    import json
                    text = response.text.strip().replace('```json', '').replace('```', '').strip()
                    data = json.loads(text)
                    for friend in data.get('friends', []):
                        self.add_friend(friend)
                    for topic in data.get('topics', []):
                        self.add_topic(topic)
                    logger.info(f"Geminiでプロフィール更新: 仲間{len(data.get('friends',[]))}人・話題{len(data.get('topics',[]))}件")
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
            words = re.findall(r'[ぁ-ん一-龥a-zA-Z]{2,8}(?:さん|くん|ちゃん)', content)
            for word in words:
                if len(word) >= 3:
                    self.add_friend(word)
