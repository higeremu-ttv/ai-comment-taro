"""
VOICEVOXエンジン クライアント v4.57（§2 改造A）

棒読みちゃん・SAPIForVOICEVOXを経由せず、太郎からVOICEVOXエンジンのHTTP APIへ
直接つないで音声(WAV)を作る。エンジン部分（画面なし）だけを使う想定。

【まだやっていないこと（配信後の統合作業）】
- gui_app.pyへの配線（起動・終了に合わせたエンジンの起動/停止を含む）
- 実際にスピーカー/OBS向け出力デバイスへ再生する部分
- エンジンが落ちている場合のフォールバック先の決定

話者ID: 2026-09-28 実機の SAPIForVOICEVOX32/StyleRegistration.xml で確認した
青山龍星「ノーマル」= 13（vault: コメント太郎 改造仕様§7）。
"""

import logging
import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:50021"
DEFAULT_SPEAKER_ID = 13  # 青山龍星・ノーマル（2026-09-28 実機確認）
DEFAULT_TIMEOUT = 10


def synthesize(text: str, speaker_id: int = DEFAULT_SPEAKER_ID,
               base_url: str = DEFAULT_BASE_URL, timeout: int = DEFAULT_TIMEOUT,
               session=None):
    """
    テキストをVOICEVOXで読み上げたWAVバイト列にして返す。
    失敗したら None（呼び出し側は棒読みちゃん等へのフォールバックを検討すること。
    §2で「Geminiが落ちたらVOICEVOXで読む」の逆＝VOICEVOXが落ちたときの扱いは未決 ❓）。

    session: requests.Session 互換オブジェクト（テスト用に差し替え可能。省略時は requests モジュール自身を使う）
    """
    if not text or not text.strip():
        return None

    http = session or requests
    try:
        # 1. audio_query: テキストから音声合成用のクエリ(アクセント等)を作る
        query_resp = http.post(
            f"{base_url}/audio_query",
            params={"text": text, "speaker": speaker_id},
            timeout=timeout,
        )
        if query_resp.status_code != 200:
            logger.warning(f"VOICEVOX audio_query 失敗: HTTP {query_resp.status_code}")
            return None
        query_json = query_resp.json()

        # 2. synthesis: クエリから実際の音声(WAV)を作る
        synth_resp = http.post(
            f"{base_url}/synthesis",
            params={"speaker": speaker_id},
            json=query_json,
            timeout=timeout,
        )
        if synth_resp.status_code != 200:
            logger.warning(f"VOICEVOX synthesis 失敗: HTTP {synth_resp.status_code}")
            return None
        return synth_resp.content

    except Exception as e:
        logger.warning(f"VOICEVOXエンジンに接続できません: {e}")
        return None


def is_engine_running(base_url: str = DEFAULT_BASE_URL, timeout: int = 3, session=None) -> bool:
    """エンジン(画面なし部分)が起動しているか確認する。太郎の起動時に使う想定。"""
    http = session or requests
    try:
        resp = http.get(f"{base_url}/version", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False
