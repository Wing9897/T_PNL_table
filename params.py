"""共用參數預設值與 kwargs 建構（CLI / 桌面 app 共用）。"""

from __future__ import annotations

MIN_PRICE_RANGE = 1
MAX_PRICE_RANGE = 300

DEFAULT_PARAMS: dict = {
    "ticker": "TSLA",
    "shares": 5,
    "commission_pct": 0.001,
    "period": "1y",
    "confidence": 0.95,
    "price_range": 30,
    "interval": 1.0,
    "interval_pct": 0.5,
    "step_mode": "dollar",
    "commission_mode": "percent",
    "commission_fixed": 0.0,
    "k_line_window_size": 5,
    "use_closing_price": True,
}

SETTINGS_KEYS = frozenset(DEFAULT_PARAMS) | {"hypothetical_price"}


def coerce_reference_price_params(
    use_closing_price: bool,
    hypothetical_price: float | None,
) -> tuple[bool, float | None]:
    """設定/請求層：無效假設價時退回收盤價（不 raise，供 app 載入設定用）。"""
    if use_closing_price:
        return True, None
    if hypothetical_price is None or hypothetical_price <= 0:
        return True, None
    return False, float(hypothetical_price)


def _to_float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_matrix_kwargs(raw: dict, *, strict_reference_price: bool = False) -> dict:
    """合併預設值、型別轉換，回傳 generate_pnl_matrix 可用的 kwargs。

    strict_reference_price:
        False → 無效假設價時退回收盤價（設定檔載入）
        True  → 保留 use_closing_price=False，由 _resolve_reference_price raise（regenerate）
    """
    p = {**DEFAULT_PARAMS, **raw}
    use_closing_requested = bool(p.get("use_closing_price", True))
    hypo = _to_float_or_none(p.get("hypothetical_price"))

    if strict_reference_price and not use_closing_requested:
        use_closing = False
        hypothetical_price = hypo
    else:
        use_closing, hypothetical_price = coerce_reference_price_params(use_closing_requested, hypo)

    price_range = int(p.get("price_range", 30))
    price_range = max(MIN_PRICE_RANGE, min(MAX_PRICE_RANGE, price_range))

    return {
        "ticker": str(p.get("ticker", "TSLA")).upper(),
        "shares": int(p.get("shares", 5)),
        "commission_pct": float(p.get("commission_pct", 0.001)),
        "period": str(p.get("period", "1y")),
        "confidence": float(p.get("confidence", 0.95)),
        "price_range": price_range,
        "interval": float(p.get("interval", 1.0)),
        "interval_pct": float(p.get("interval_pct", 0.5)),
        "step_mode": str(p.get("step_mode", "dollar")),
        "commission_mode": str(p.get("commission_mode", "percent")),
        "commission_fixed": float(p.get("commission_fixed", 0.0)),
        "k_line_window_size": int(p.get("k_line_window_size", 5)),
        "use_closing_price": use_closing,
        "hypothetical_price": hypothetical_price,
    }
