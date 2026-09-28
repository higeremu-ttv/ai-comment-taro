"""
Gemini TTS クライアント v4.57（§2-3 英語読み上げ・§3 太郎の声の土台）

英語コメントの読み上げ・太郎自身の声を Gemini TTS で作る。VOICEVOXは英語が
苦手なため（2026-09-28 おじさん決定、§2）、英語だけこちらを使う。

【使用SDKについて】
comment_generator.py 等の既存コードは旧SDK `google.generativeai`（2026-09時点で
サポート終了）を使っている。TTSは新SDK `google.genai` でのみ動作を実機確認できたため、
このモジュールだけ新SDKを使う。将来的に既存コードも新SDKへ寄せる余地はあるが、
それは本改造とは別件（提案は別途）。

【モデル名について】
2026-09-29 実際にAPIへ models.list() を投げて確認した、現存するTTS対応モデル：
  gemini-2.5-flash-preview-tts / gemini-2.5-pro-preview-tts（旧世代）
  gemini-3.1-flash-tts-preview
  gemini-3.8-flash-tts / gemini-3.8-flash-lite-tts（最新）
太郎の他機能の「Lite=定型作業、通常=品質が要る場面」という使い分け（CLAUDE.md）に
倣い、既定は軽量な gemini-3.8-flash-lite-tts。声の表現力が要る場面（§3 太郎の声の
俳句・謎かけ等）は呼び出し側で gemini-3.8-flash-tts を指定できるようにしてある。

レスポンスの形（2026-09-29 実際に1回呼んで確認済み）：
  candidates[0].content.parts[0].inline_data に mime_type="audio/wav" のWAVバイト列
  （RIFFヘッダー込み・そのままファイルに保存して再生できる）。

【まだやっていないこと（配信後の統合作業）】
- gui_app.pyへの配線（英語コメント判定→ここを呼ぶ）
- Gemini側が落ちたときにVOICEVOXへフォールバックする実配線（§2で決定済みの挙動だが
  まだコードにしていない）
- 太郎自身の声としての口調指定（俳句・謎かけの「間」）
"""

import logging

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.8-flash-lite-tts"
DEFAULT_VOICE = "Kore"

# 太郎自身の声（§3）。VOICEVOX（視聴者コメント読み上げ）とは元々エンジンが違うので
# 声質は自然に分かれるが、声の名前自体は 🟡 まだおじさんが聞いて決めていない仮の値。
# 配信後に実際に聞き比べて、必要ならここを変える。
TARO_VOICE = "Kore"


def synthesize(text: str, api_key: str, model: str = DEFAULT_MODEL,
               voice_name: str = DEFAULT_VOICE, client=None):
    """
    テキストをGemini TTSで読み上げたWAVバイト列にして返す。
    失敗したら None（呼び出し側でVOICEVOXへのフォールバックを検討すること）。

    client: google.genai.Client 互換オブジェクト（テスト用に差し替え可能。
            省略時は google.genai.Client(api_key=api_key) を新規に作る）
    """
    if not text or not text.strip():
        return None
    if not api_key and client is None:
        logger.warning("Gemini TTS: APIキーが設定されていません")
        return None

    try:
        from google import genai
        from google.genai import types

        genai_client = client or genai.Client(api_key=api_key)
        resp = genai_client.models.generate_content(
            model=model,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
                    )
                ),
            ),
        )
        candidates = getattr(resp, "candidates", None)
        if not candidates:
            logger.warning("Gemini TTS: 応答にcandidatesがありません")
            return None
        parts = candidates[0].content.parts
        if not parts or not getattr(parts[0], "inline_data", None):
            logger.warning("Gemini TTS: 応答に音声データがありません")
            return None
        return parts[0].inline_data.data

    except Exception as e:
        logger.warning(f"Gemini TTSに接続できません: {e}")
        return None


def build_styled_text(text: str, style: str = "") -> str:
    """
    口調の指示を文章に含める（§3「俳句・謎かけは口調を指示」）。
    Gemini TTSは指示を専用の項目ではなく地の文で受け取る作りのため、
    話し方の指示を頭に付けて渡す。
    🔍 実際に聞いてどのくらい効くかは配信後の確認事項（音を出して初めて分かるため）。
    """
    if not style:
        return text
    return f"（{style}）\n{text}"


def speak_as_taro(text: str, api_key: str, style: str = "",
                   model: str = DEFAULT_MODEL, voice_name: str = TARO_VOICE, client=None):
    """太郎自身の声として読み上げる（§3）。styleは俳句・謎かけ等の口調指示。"""
    styled = build_styled_text(text, style)
    return synthesize(styled, api_key=api_key, model=model, voice_name=voice_name, client=client)
