"""
読み上げ辞書モジュール v4.57（§2-1）

チャットに書かれた文字を、読み上げる直前にどう読むかへ置き換える。
既存の「訂正辞書」（audio_module / profile_manager、聞き間違えた文字→正しい文字）とは向きが逆。
  訂正辞書: おじさんの声 → 太郎（聞き取り側）
  読み上げ辞書: チャット → 声（読み上げ側）

VOICEVOX（日本語）にも Gemini TTS（英語）にも、読み上げ直前に同じ辞書を通す想定。
まだ gui_app.py には配線していない（配信中に音声チェーンへ影響しないため。統合は配信後）。

【種類】
- words: 単純な置き換え（部分文字列マッチ）。「草」→「くさ」、固有名詞の読み方など
- patterns: 正規表現での置き換え（並びの置き換え）。「ｗｗｗ」→「わらわら」、URL省略など

保存先は learned_profile.json と同じ扱い（JSON・gitignore対象・base_dir配下）。
初期データは TTA（TwitchTalkApp）のエモート置換とチャンネル絵文字置換、
棒読みちゃんのユーザー追加辞書を 2026-09-28 に実機で確認した値から起こした（vault: コメント太郎 改造仕様）。
"""

import json
import os
import re
import logging

logger = logging.getLogger(__name__)

DICT_FILE = "reading_dictionary.json"
MAX_WORDS = 200
MAX_PATTERNS = 60


def _seed_dictionary() -> dict:
    """初期データ。2026-09-28 TTA・棒読みちゃんの実設定から移植（🟡おじさん未最終承認、たたき台）。"""
    return {
        "words": {
            # --- 棒読みちゃんのユーザー追加辞書（既定辞書との差分、2026-09-28確認）---
            "AI": "エーアイ",
            "味方": "みかた",
            "宝物": "たからもの",
            "桃煌": "ももきら",
            "滑舌": "かつぜつ",
            "BTK": "バタケ",
            "GTA": "ジーティーエー",
            "安定性": "アンテイセイ",
            "誤変換": "ごへんかん",
            "金武器": "きんぶき",
            "FIVEM": "ファイブエム",
            "未成年の方": "みせいねんのかた",
            "TWITCH": "トゥウィッチ",
            "いただいた方": "いただいたかた",
            "FALLGUYS": "フォールガイズ",
            "BLUETOOTH": "ブルートゥース",
            # --- TTA のチャンネル絵文字置換（英語コメント内、2026-09-28確認）---
            "higereEnd": "Wasted",
            "higereGg": "GG",
            "higereNeta": "You're sleeping, aren't you?",
            "higereMajide": "are you kidding me?",
            "higereDontmind": "don't mind",
            "higereGuruguru": "rolling",
            "higereVictory": "Victory royale",
            "higereNeruna": "don't sleep",
            "higereJiji": "jiji jiji",
            "higereOtsu": "what's up?",
            "higereNeru": "just sleep now",
            "higereRaid": "Raid",
            "higereOji": "fight",
            "higereNaisu2": "nice",
            "higereNf": "nice fight",
            "higereHaa": "Huh?",
            "higereMove": "trembling",
            "higereShinda": "wasted",
            # --- 仕様ノート §2-1 で例示された基本の読み替え ---
            "草": "くさ",
        },
        "patterns": [
            # 並びの置き換え（正規表現）。仕様ノート §2-1 の例。
            {"pattern": r"[wｗWＷ]{2,}", "repl": "わらわら"},
            {"pattern": r"https?://\S+", "repl": "ゆーあーるえる"},
        ],
    }


def load_dictionary(base_dir: str = "") -> dict:
    """辞書を読み込む。ファイルが無ければ初期データで新規作成する。"""
    base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, DICT_FILE)
    if not os.path.exists(path):
        data = _seed_dictionary()
        save_dictionary(data, base_dir)
        return data
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("words", {})
        data.setdefault("patterns", [])
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"読み上げ辞書の読み込みに失敗、初期データで再作成します: {e}")
        data = _seed_dictionary()
        save_dictionary(data, base_dir)
        return data


def save_dictionary(data: dict, base_dir: str = "") -> None:
    base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, DICT_FILE)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def add_word(data: dict, word: str, reading: str) -> dict:
    """声で『太郎、読み方覚えて。〇〇は△△』と言われたときに使う想定（v4.50の仕組みに合わせる）。"""
    words = data.setdefault("words", {})
    words[word] = reading
    if len(words) > MAX_WORDS:
        # 最も古い（先に足された）ものから間引く
        oldest = next(iter(words))
        if oldest != word:
            del words[oldest]
    return data


def apply_reading(text: str, data: dict) -> str:
    """読み上げ直前にテキストへ辞書を適用する。words → patterns の順。"""
    if not text:
        return text
    result = text
    for word, reading in data.get("words", {}).items():
        if word in result:
            result = result.replace(word, reading)
    for entry in data.get("patterns", []):
        try:
            result = re.sub(entry["pattern"], entry["repl"], result)
        except re.error as e:
            logger.warning(f"読み上げ辞書の正規表現が不正: {entry.get('pattern')} ({e})")
    return result
