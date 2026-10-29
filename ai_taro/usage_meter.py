"""v4.62: Gemini の使用量と料金の概算を数える。

太郎が呼ぶ Gemini（文章・抽出・検索・声）の使ったトークン数をモデルごとに足し上げ、
配信の途中と終わりに「いくらかかったか」を記録に出す。
単価は Google 公式の料金表（ai.google.dev/gemini-api/docs/pricing、2026-10-30 確認）。
思考トークンは出力として課金されるので、出力に含めて数える。
"""
import datetime
import logging
import threading

logger = logging.getLogger(__name__)

# 100万トークンあたりのドル（入力, 出力）。声のモデルの出力は音声トークン
PRICES = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.8-flash-lite-tts": (0.50, 6.00),
    "gemini-3.8-flash-tts": (0.50, 9.00),
}
# 2027-01-01 から値上げが決まっているもの
PRICES_FROM_2027 = {
    "gemini-3.8-flash": (1.50, 7.50),
    "gemini-3.8-flash-lite-tts": (1.00, 12.00),
    "gemini-3.8-flash-tts": (1.00, 18.00),
}

_lock = threading.Lock()
_totals = {}  # model -> {"calls", "in", "out"}


def _clean_name(model) -> str:
    name = str(model or "")
    return name.split("/")[-1]


def price_of(model: str, today: datetime.date = None):
    """(入力, 出力) の単価。表に無いモデルは None"""
    model = _clean_name(model)
    today = today or datetime.date.today()
    if today >= datetime.date(2027, 1, 1) and model in PRICES_FROM_2027:
        return PRICES_FROM_2027[model]
    return PRICES.get(model)


def record(model, in_tokens: int, out_tokens: int):
    """1回の呼び出しの使用量を足す"""
    model = _clean_name(model)
    if not model:
        return
    with _lock:
        t = _totals.setdefault(model, {"calls": 0, "in": 0, "out": 0})
        t["calls"] += 1
        t["in"] += int(in_tokens or 0)
        t["out"] += int(out_tokens or 0)


def record_sdk(model, response):
    """google.generativeai の応答から使用量を拾う（拾えなければ何もしない）"""
    try:
        um = getattr(response, "usage_metadata", None)
        if um is None:
            return
        out = (getattr(um, "candidates_token_count", 0) or 0) + (getattr(um, "thoughts_token_count", 0) or 0)
        record(model, getattr(um, "prompt_token_count", 0) or 0, out)
    except Exception:
        pass


def record_rest(model, data: dict):
    """REST の応答（JSON）から使用量を拾う"""
    try:
        um = (data or {}).get("usageMetadata") or {}
        if not um:
            return
        out = (um.get("candidatesTokenCount") or 0) + (um.get("thoughtsTokenCount") or 0)
        record(model, um.get("promptTokenCount") or 0, out)
    except Exception:
        pass


def reset():
    with _lock:
        _totals.clear()


def snapshot() -> dict:
    with _lock:
        return {k: dict(v) for k, v in _totals.items()}


def cost_usd(totals: dict = None, today: datetime.date = None):
    """(合計ドル, 単価が分からなかったモデルの一覧)"""
    totals = snapshot() if totals is None else totals
    total, unknown = 0.0, []
    for model, t in totals.items():
        p = price_of(model, today)
        if p is None:
            unknown.append(model)
            continue
        total += t["in"] / 1e6 * p[0] + t["out"] / 1e6 * p[1]
    return total, unknown


def report_lines(usd_jpy: float = 150.0, title: str = "ここまで") -> list:
    """記録に出す文。1行目が合計、続けてモデルごと"""
    totals = snapshot()
    if not totals:
        return [f"[費用] {title}: Gemini の利用なし"]
    total, unknown = cost_usd(totals)
    lines = [f"[費用] {title}の概算: 約{total * usd_jpy:.1f}円（${total:.4f}、1ドル={usd_jpy:g}円で計算）"]
    for model, t in sorted(totals.items()):
        p = price_of(model)
        yen = (t["in"] / 1e6 * p[0] + t["out"] / 1e6 * p[1]) * usd_jpy if p else None
        yen_txt = f"約{yen:.1f}円" if yen is not None else "単価不明"
        lines.append(f"[費用]   {model}: {t['calls']}回 入力{t['in']:,} 出力{t['out']:,}トークン → {yen_txt}")
    if unknown:
        lines.append(f"[費用]   単価表に無いモデルは合計に入っていません: {', '.join(unknown)}")
    return lines


def log_report(usd_jpy: float = 150.0, title: str = "ここまで"):
    for line in report_lines(usd_jpy, title):
        logger.info(line)
