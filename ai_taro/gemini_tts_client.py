"""
Gemini TTS クライアント v4.58（§2-3 英語読み上げ・§3 太郎の声）

英語コメントの読み上げ・太郎自身の声を Gemini TTS で作る。VOICEVOXは英語が
苦手なため、英語だけこちらを使う。

【v4.58: SDKをやめてHTTPで直接呼ぶ形に変更】
v4.57 は口調の指示を本文の頭に「（口調）」と書き足していたが、これだと Gemini が
指示文まで声に出して読むことがあった（俳句のセリフで3回中2回。音声を faster-whisper で
文字起こしして確認）。試した結果（2026-09-29〜30）:
  - 本文の頭に日本語で書く …… 3回中2回漏れる（v4.57の方式）
  - 英語の前置き "Say in this tone (…):" …… 3回中2回漏れる
  - system_instruction …… 全TTSモデルで「Developer instruction is not enabled」で使えない
  - parts の "speech_metadata": {"style": …} …… 6回中0回。これを採用
speech_metadata は SDK（google-genai）の型に無いので、VOICEVOXと同じく requests で直接呼ぶ。
あわせて speechConfig.languageCode を指定できるようにした（太郎の声は ja-JP 固定の方が
自然と判定された）。

【モデル名】2026-09-29 models.list() で確認:
  gemini-3.8-flash-tts / gemini-3.8-flash-lite-tts（最新）ほか。既定は軽量な lite。
  上位版 gemini-3.8-flash-tts は1分10回まで（429 GenerateRequestsPerMinutePerProjectPerModel）。
  失敗時は呼び出し側（read_aloud.py）がVOICEVOXで読む。

レスポンス: candidates[0].content.parts[0].inlineData.data が base64 の WAV（RIFFヘッダー込み）。
"""

import base64
import logging
import threading
import time
from collections import deque

import requests

logger = logging.getLogger(__name__)

# ------------------------------------------------------------
# v4.59: 1分あたりの回数を数える（上位版は1分10回まで。断られる前に自分で止める）
# ------------------------------------------------------------
_rate_lock = threading.Lock()
_rate_limits = {}      # {model: 1分あたりに使ってよい回数}（set_rate_limit で設定。未設定は数えるだけ）
_rate_calls = {}       # {model: deque[頼んだ時刻]}
_blocked_until = {}    # {model: この時刻までは使わない}（429で言われた待ち時間）


def set_rate_limit(model: str, per_minute: int):
    """このモデルを1分に何回まで使うか（0以下なら制限しない）"""
    with _rate_lock:
        if per_minute and per_minute > 0:
            _rate_limits[model] = int(per_minute)
        else:
            _rate_limits.pop(model, None)


def calls_last_minute(model: str) -> int:
    with _rate_lock:
        q = _rate_calls.get(model)
        if not q:
            return 0
        now = time.time()
        while q and now - q[0] > 60:
            q.popleft()
        return len(q)


def _try_reserve(model: str) -> bool:
    """今このモデルを使ってよいか。よければ回数に数えて True"""
    now = time.time()
    with _rate_lock:
        if now < _blocked_until.get(model, 0):
            logger.info(f"Gemini TTS（{model}）: 上限の待ち時間中のため使いません"
                        f"（あと{int(_blocked_until[model] - now)}秒）")
            return False
        q = _rate_calls.setdefault(model, deque())
        while q and now - q[0] > 60:
            q.popleft()
        limit = _rate_limits.get(model)
        if limit and len(q) >= limit:
            logger.info(f"Gemini TTS（{model}）: 1分あたり{limit}回に達したので、今回は使いません")
            return False
        q.append(now)
        return True


def _learn_from_429(model: str, resp):
    """429の本文から待ち時間を読み取り、その間はこのモデルを使わない"""
    delay = 60.0
    try:
        for d in resp.json().get("error", {}).get("details", []):
            rd = d.get("retryDelay")
            if rd:
                delay = float(str(rd).rstrip("s")) + 1
    except Exception:
        pass
    with _rate_lock:
        _blocked_until[model] = time.time() + delay


def _reset_rate_state():
    """テスト用"""
    with _rate_lock:
        _rate_limits.clear(); _rate_calls.clear(); _blocked_until.clear()

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.8-flash-lite-tts"
DEFAULT_VOICE = "Kore"
DEFAULT_TIMEOUT = 60

# 太郎自身の声の既定値（実際の値は config.py の TARO_VOICE_* を使う）
TARO_VOICE = "Algieba"


def synthesize(text: str, api_key: str, model: str = DEFAULT_MODEL,
               voice_name: str = DEFAULT_VOICE, style: str = "", language_code: str = "",
               timeout: int = DEFAULT_TIMEOUT, session=None):
    """
    テキストをGemini TTSで読み上げたWAVバイト列にして返す。失敗したら None。

    style: 話し方の指示。本文とは別の枠（speech_metadata.style）で渡すので読み上げられない
    language_code: 例 "ja-JP"。空なら Gemini の自動判定
    session: requests 互換オブジェクト（テスト用に差し替え可能）
    """
    if not text or not text.strip():
        return None
    if not api_key:
        logger.warning("Gemini TTS: APIキーが設定されていません")
        return None

    part = {"text": text}
    if style:
        part["speech_metadata"] = {"style": style}
    speech_config = {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name}}}
    if language_code:
        speech_config["languageCode"] = language_code
    body = {
        "contents": [{"role": "user", "parts": [part]}],
        "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": speech_config},
    }

    if not _try_reserve(model):
        return None

    http = session or requests
    try:
        resp = http.post(f"{API_BASE}/{model}:generateContent",
                         params={"key": api_key}, json=body, timeout=timeout)
        if resp.status_code != 200:
            logger.warning(f"Gemini TTS エラー（{model}）: {_describe_error(resp)}")
            if resp.status_code == 429:
                _learn_from_429(model, resp)
            return None
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            logger.warning("Gemini TTS: 応答にcandidatesがありません")
            return None
        parts = (candidates[0].get("content") or {}).get("parts") or []
        inline = parts[0].get("inlineData") if parts else None
        if not inline or not inline.get("data"):
            logger.warning("Gemini TTS: 応答に音声データがありません")
            return None
        return base64.b64decode(inline["data"])
    except Exception as e:
        logger.warning(f"Gemini TTSに接続できません: {type(e).__name__}")
        return None


def _describe_error(resp) -> str:
    """エラーを短く日本語で。429 はどの上限（1分あたり／1日あたり）に当たったかを出す"""
    try:
        err = resp.json().get("error", {})
        if resp.status_code == 429:
            for d in err.get("details", []):
                for v in d.get("violations", []):
                    qid = v.get("quotaId", "")
                    kind = "1分あたり" if "PerMinute" in qid else ("1日あたり" if "PerDay" in qid else qid)
                    return f"利用上限（{kind}{v.get('quotaValue', '')}回）に達しました"
            return "利用上限に達しました"
        return f"HTTP {resp.status_code} {(err.get('message') or '')[:80]}"
    except Exception:
        return f"HTTP {resp.status_code}"


def apply_replacements(text: str, replacements: str) -> str:
    """声にするときだけの読み替え。書式は "ひげさん=ヒゲさん,別の語=読み"（カンマ区切り）。
    チャットに投稿する文は変えない。Geminiの読み方がおかしい語を直すために使う"""
    if not text or not replacements:
        return text
    for pair in replacements.split(","):
        if "=" not in pair:
            continue
        src, dst = pair.split("=", 1)
        src, dst = src.strip(), dst.strip()
        if src:
            text = text.replace(src, dst)
    return text


def speak_as_taro(text: str, api_key: str, style: str = "", model: str = DEFAULT_MODEL,
                  voice_name: str = TARO_VOICE, language_code: str = "ja-JP",
                  replacements: str = "", session=None):
    """太郎自身の声として読み上げる（§3）。口調(style)は別枠で渡すので声に出ない"""
    return synthesize(apply_replacements(text, replacements), api_key=api_key, model=model,
                      voice_name=voice_name, style=style, language_code=language_code,
                      session=session)
