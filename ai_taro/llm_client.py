"""
LLM接続モジュール v4.40（マルチAI対応）

会話用AIをGemini以外に差し替えるための「変換アダプタ」。
OpenAI互換形式（業界標準の呼び出し方）に対応しており、これ1つで
以下のサービスにつながる：

- OpenAI            : https://api.openai.com/v1        （gpt-4o-mini 等）
- OpenRouter        : https://openrouter.ai/api/v1     （Claude含む100種以上の窓口）
- Ollama（ローカル）: http://localhost:11434/v1        （無料・要VRAM相談）
- LM Studio         : http://localhost:1234/v1
- Gemini互換入口    : https://generativelanguage.googleapis.com/v1beta/openai/

設計方針（BYOK = Bring Your Own Key）:
- APIキーは使う人が自分で用意する。太郎の作者は利用料を負担しない
- 接続に失敗しても呼び出し側（comment_generator）がGemini Liteに退避するので
  コメントが止まることはない
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class OpenAICompatClient:
    """OpenAI互換APIのシンプルなクライアント。

    requestsだけで実装（追加ライブラリ不要）。
    """

    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: float = 20.0):
        self.base_url = (base_url or "").rstrip('/')
        self.api_key = api_key or ""
        self.model = model or ""
        self.timeout = timeout

    def is_configured(self) -> bool:
        """接続に必要な設定がそろっているか（ローカルLLMはキー不要）"""
        return bool(self.base_url and self.model)

    def chat(self, system_prompt: str, user_prompt: str,
             max_tokens: int = 300, temperature: float = 0.9) -> Optional[str]:
        """1回の生成を行う。失敗したらNoneを返す（例外は投げない）。"""
        if not self.is_configured():
            logger.warning("OpenAI互換APIの設定が不完全です（接続先URLまたはモデル名が未設定）")
            return None
        try:
            import requests

            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt or ""},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }

            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                timeout=self.timeout,
            )

            if resp.status_code != 200:
                # エラーの中身を短くログに残す（キー間違い・残高切れ等の切り分け用）
                body = resp.text[:200] if resp.text else ""
                logger.warning(f"OpenAI互換API エラー: HTTP {resp.status_code} {body}")
                return None

            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                logger.warning("OpenAI互換API: 応答が空でした")
                return None
            content = (choices[0].get("message", {}) or {}).get("content", "")
            return content.strip() if content else None

        except Exception as e:
            logger.warning(f"OpenAI互換API 接続失敗: {e}")
            return None


def build_smart_client(config) -> Optional[OpenAICompatClient]:
    """configの設定から会話用の外部AIクライアントを作る。
    SMART_PROVIDER が "openai" のときだけ返す（それ以外はNone＝Geminiを使う）。"""
    provider = (getattr(config, 'SMART_PROVIDER', 'gemini') or 'gemini').lower()
    if provider != 'openai':
        return None
    return OpenAICompatClient(
        base_url=getattr(config, 'OPENAI_BASE_URL', ''),
        api_key=getattr(config, 'OPENAI_API_KEY', ''),
        model=getattr(config, 'OPENAI_MODEL', ''),
    )
