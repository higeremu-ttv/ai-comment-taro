"""
読み上げパイプライン v4.57（§2 改造Aの統合部）

これまでの部品（読み上げ辞書・除外リスト・VOICEVOX・Gemini TTS）を1本につなぐ。
視聴者コメント1件を受け取り、読むべきかどうかを判定し、辞書を通し、
言語を判定し、日本語ならVOICEVOX、英語ならGemini TTS（失敗時はVOICEVOXへ
フォールバック＝✅2026-09-28おじさん決定）で音声化する。

【まだやっていないこと（配信後の統合作業）】
- gui_app.pyへの配線（Twitchのコメント受信→ここを呼ぶ→実際に再生）
- 名前を読むかどうかの制御（TTAの設定=オフに合わせる。§7で確認済みだがここには未反映）
- 太郎の発言との再生キュー統合（§3）
"""

import logging
import re

import reading_dictionary
import reading_exclusions
import voicevox_client
import gemini_tts_client

logger = logging.getLogger(__name__)

# 🟡 言語判定は文字種で機械的に（§2）。かな・漢字を含まない＝英語扱い、混在は日本語扱い。
_JAPANESE_CHAR_RE = re.compile(
    r"[぀-ゟ゠-ヿ一-鿿]"  # ひらがな・カタカナ・漢字
)


def detect_language(text: str) -> str:
    """"ja" か "en" を返す。かな・漢字が1文字でも含まれれば日本語扱い。"""
    if _JAPANESE_CHAR_RE.search(text or ""):
        return "ja"
    return "en"


def read_comment(username: str, text: str, gemini_api_key: str,
                  dictionary_data: dict = None, exclusions_data: dict = None,
                  voicevox_synthesize=voicevox_client.synthesize,
                  gemini_synthesize=gemini_tts_client.synthesize):
    """
    視聴者コメント1件を読み上げ音声(WAVバイト列)にする。
    読まない判定・合成失敗のときは None。

    voicevox_synthesize / gemini_synthesize は差し替え可能（テスト用）。
    dictionary_data / exclusions_data を省略すると、その場でファイルから読み込む
    （呼び出しのたびに読み込むと非効率なので、実配線時は呼び出し側でキャッシュして渡すこと）。
    """
    if exclusions_data is None:
        exclusions_data = reading_exclusions.load_exclusions()
    if not reading_exclusions.should_read(username, exclusions_data):
        return None

    if dictionary_data is None:
        dictionary_data = reading_dictionary.load_dictionary()
    reading_text = reading_dictionary.apply_reading(text, dictionary_data)
    if not reading_text or not reading_text.strip():
        return None

    lang = detect_language(reading_text)

    if lang == "ja":
        return voicevox_synthesize(reading_text)

    # 英語: Gemini TTSが基本。落ちたらVOICEVOXへフォールバック（✅決定事項）
    audio = gemini_synthesize(reading_text, api_key=gemini_api_key)
    if audio is not None:
        return audio
    logger.warning("Gemini TTSが使えないため、英語コメントもVOICEVOXで読みます")
    return voicevox_synthesize(reading_text)
