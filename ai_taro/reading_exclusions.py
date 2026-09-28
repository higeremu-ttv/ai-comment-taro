"""
読み上げ除外リストモジュール v4.57（§2 TTAからの引き継ぎ）

視聴者コメントを読み上げる／読み上げない、を決めるアカウント一覧。
config.py の EXCLUDED_ACCOUNTS（太郎自身が反応・生成しない対象）とは別物：
こちらは「読み上げ役」に統合したあとの、声を出すか出さないかの設定。

初期データは TwitchTalkApp（TTA）の blockUser 設定を 2026-09-28 に実機で読み、
おじさんに画面で確認してもらって特定した値（vault: コメント太郎 改造仕様§7）。
TTA では新規コメント者が自動で「読む」に追加され、下記の4人だけ手動で「読まない」に
されていた。
"""

import json
import os
import logging

logger = logging.getLogger(__name__)

EXCLUSIONS_FILE = "reading_exclusions.json"

# 2026-09-28 TTAの画面でおじさんに確認済み（false=読まない、の4人）
SEED_EXCLUDED_USERS = [
    "wizebot",
    "higeremu_translate",
    "sery_bot",
    "higeremu_tr",
]


def _seed_exclusions() -> dict:
    return {"excluded_users": list(SEED_EXCLUDED_USERS)}


def load_exclusions(base_dir: str = "") -> dict:
    base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, EXCLUSIONS_FILE)
    if not os.path.exists(path):
        data = _seed_exclusions()
        save_exclusions(data, base_dir)
        return data
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("excluded_users", [])
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"読み上げ除外リストの読み込みに失敗、初期データで再作成します: {e}")
        data = _seed_exclusions()
        save_exclusions(data, base_dir)
        return data


def save_exclusions(data: dict, base_dir: str = "") -> None:
    base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, EXCLUSIONS_FILE)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def should_read(username: str, data: dict) -> bool:
    """このユーザーのコメントを読み上げてよいか。大文字小文字を区別しない（TTAの仕様に合わせる）。"""
    if not username:
        return True
    excluded = {u.lower() for u in data.get("excluded_users", [])}
    return username.lower() not in excluded


def add_exclusion(data: dict, username: str) -> dict:
    excluded = data.setdefault("excluded_users", [])
    if username.lower() not in {u.lower() for u in excluded}:
        excluded.append(username)
    return data


def remove_exclusion(data: dict, username: str) -> dict:
    excluded = data.setdefault("excluded_users", [])
    data["excluded_users"] = [u for u in excluded if u.lower() != username.lower()]
    return data
