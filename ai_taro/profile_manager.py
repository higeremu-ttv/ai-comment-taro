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

    def update_from_conversation(self, conversation_history: list):
        """会話履歴からプロフィールを更新する（配信終了時に呼ぶ）"""
        if not conversation_history:
            return

        # 仲間の名前を抽出（簡易）
        import re
        friend_patterns = ['さん', 'くん', 'ちゃん']
        for msg in conversation_history:
            content = msg.get('content', '')
            # 「あおちゃん」「beeteekee」などのパターンを検出
            words = re.findall(r'[ぁ-ん一-龥a-zA-Z]{2,8}(?:さん|くん|ちゃん)', content)
            for word in words:
                if len(word) >= 3:
                    self.add_friend(word)

        self.save()
        logger.info("会話履歴からプロフィールを更新しました")
