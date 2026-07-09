"""
PnL Matrix 產生器
=================
下載股票歷史數據，計算「買入價 × 賣出價」損益矩陣、基於相似 K 線形態的信心值，
以及 VaR / CVaR 風險指標，輸出為前端 (pnl_matrix.html) 使用的 matrix_data.json。

用法:
    python pnl_matrix.py                      # 使用預設參數
    python pnl_matrix.py --ticker MSFT --shares 10 --price-range 12
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from params import MAX_PRICE_RANGE, MIN_PRICE_RANGE, build_matrix_kwargs

OUTPUT_FILE = "matrix_data.json"
MIN_K_LINE_WINDOW = 2


def _resolve_reference_price(
    closing_price: float,
    use_closing_price: bool,
    hypothetical_price: float | None,
) -> float:
    """決定矩陣中心與風險基準的參考價位。"""
    if use_closing_price:
        return closing_price
    if hypothetical_price is None or hypothetical_price <= 0:
        raise ValueError("未勾選收盤價時，請輸入有效的假設價（必須大於 0）。")
    return float(hypothetical_price)


def _cosine_similarity_to_target(patterns_arr: np.ndarray, target: np.ndarray) -> np.ndarray:
    target_flat = target.ravel()
    dots = patterns_arr @ target_flat
    norms_p = np.linalg.norm(patterns_arr, axis=1)
    norm_t = np.linalg.norm(target_flat)
    return np.nan_to_num(dots / (norms_p * norm_t + 1e-12))


def _extract_close_series(hist: pd.DataFrame, ticker: str) -> pd.Series:
    if isinstance(hist.columns, pd.MultiIndex):
        close = hist["Close"]
        if isinstance(close, pd.DataFrame):
            close = close[ticker] if ticker in close.columns else close.iloc[:, 0]
    else:
        close = hist["Close"]
    return pd.to_numeric(close, errors="coerce").dropna()


def _compute_confidence(
    closes: np.ndarray,
    reference_price: float,
    price_levels: np.ndarray,
    interval: float,
    window: int,
) -> np.ndarray:
    patterns = []
    outcomes = []
    for i in range(len(closes) - window):
        win = closes[i : i + window]
        patterns.append(win - win[0])
        outcomes.append(closes[i + window] - win[-1])

    patterns_arr = np.asarray(patterns)
    outcomes_arr = np.asarray(outcomes)
    target = (closes[-window:] - closes[-window]).reshape(1, -1)
    similarities = _cosine_similarity_to_target(patterns_arr, target)
    weights = (similarities + 1.0) / 2.0 + 1e-9
    projected = reference_price + outcomes_arr

    bins = np.append(price_levels, price_levels[-1] + interval)
    prob_dist, _ = np.histogram(projected, bins=bins, weights=weights)
    total = prob_dist.sum()
    if total > 0:
        return prob_dist / total
    return np.ones(len(price_levels)) / len(price_levels)


def _compute_loss_prob(returns_arr: np.ndarray, price_levels: np.ndarray, reference_price: float) -> np.ndarray:
    level_returns = price_levels / reference_price - 1.0
    return (returns_arr[None, :] < level_returns[:, None]).mean(axis=1) * 100.0


def _download_closes(
    ticker: str,
    period: str,
    k_line_window_size: int,
) -> tuple[pd.Series, np.ndarray, int]:
    """下載收盤價並檢查數據充足性。回傳 (closes_series, close_prices, 調整後窗口)。"""
    print(f"開始為 {ticker} 生成數據...")
    hist = yf.download(ticker, period=period, progress=False, auto_adjust=True)
    if hist is None or hist.empty:
        raise ValueError(f"無法下載 {ticker} 的數據，請檢查 ticker 是否正確。")

    closes_series = _extract_close_series(hist, ticker)
    close_prices = closes_series.to_numpy(dtype=float)

    if len(close_prices) < 30:
        print(f"警告：歷史數據少於30天（僅 {len(close_prices)} 天），K線窗口將縮小。")
        k_line_window_size = min(k_line_window_size, max(1, len(close_prices) - 3))
    if len(close_prices) < k_line_window_size + 2:
        raise ValueError(
            f"數據不足（需要至少 {k_line_window_size + 2} 天，"
            f"但只有 {len(close_prices)} 天）無法進行計算。"
        )
    return closes_series, close_prices, k_line_window_size


def _build_price_grid(
    reference_price: float,
    price_range: int,
    step_mode: str,
    interval: float,
    interval_pct: float,
) -> tuple[float, np.ndarray]:
    step = reference_price * (interval_pct / 100.0) if step_mode == "percent" else interval
    if step <= 0:
        raise ValueError("價格間距必須大於 0。")
    price_levels = np.round(
        np.arange(
            reference_price - price_range * step,
            reference_price + (price_range + 1) * step,
            step,
        ),
        2,
    )
    return float(step), price_levels


def _compute_risk_metrics(
    closes_series: pd.Series,
    close_prices: np.ndarray,
    reference_price: float,
    price_levels: np.ndarray,
    confidence: float,
) -> dict[str, Any]:
    returns = closes_series.pct_change().dropna()
    var_pct = float(returns.quantile(1 - confidence))
    tail = returns[returns <= var_pct]
    cvar_pct = float(tail.mean()) if not tail.empty else var_pct * 1.5

    returns_arr = returns.to_numpy(dtype=float)
    loss_prob = _compute_loss_prob(returns_arr, price_levels, reference_price)

    running_max = np.maximum.accumulate(close_prices)
    drawdowns = close_prices / running_max - 1.0
    trough_idx = int(np.argmin(drawdowns))
    peak_idx = int(np.argmax(close_prices[: trough_idx + 1])) if trough_idx > 0 else 0
    mdd_pct = float(drawdowns[trough_idx])
    idx = closes_series.index

    return {
        "var_price": reference_price * (1 + var_pct),
        "cvar_price": reference_price * (1 + cvar_pct),
        "var_pct": var_pct,
        "cvar_pct": cvar_pct,
        "loss_prob": loss_prob,
        "max_drawdown_pct": mdd_pct,
        "mdd_peak_price": float(close_prices[peak_idx]),
        "mdd_trough_price": float(close_prices[trough_idx]),
        "mdd_peak_date": idx[peak_idx].strftime("%Y-%m-%d"),
        "mdd_trough_date": idx[trough_idx].strftime("%Y-%m-%d"),
    }


def _format_output(
    *,
    ticker: str,
    shares: int,
    commission_pct: float,
    commission_mode: str,
    commission_fixed: float,
    closing_price: float,
    reference_price: float,
    use_closing_price: bool,
    confidence: float,
    k_line_window_size: int,
    period: str,
    step_mode: str,
    interval: float,
    interval_pct: float,
    step: float,
    closes_series: pd.Series,
    close_prices: np.ndarray,
    price_levels: np.ndarray,
    prob_dist: np.ndarray,
    risk: dict[str, Any],
) -> dict[str, Any]:
    """組裝前端/JSON 輸出。shares 僅供前端預設，不參與後端計算。"""
    prob_pct = [round(float(p * 100), 2) for p in prob_dist]
    comm_value = commission_fixed if commission_mode == "fixed" else commission_pct

    return {
        "ticker": ticker,
        "shares": shares,
        "commission_pct": commission_pct,
        "commission_mode": commission_mode,
        "commission_value": round(float(comm_value), 6),
        "reference_price": round(reference_price, 2),
        "closing_price": round(closing_price, 2),
        "use_closing_price": use_closing_price,
        "hypothetical_price": round(reference_price, 2) if not use_closing_price else None,
        "var_price": round(risk["var_price"], 2),
        "cvar_price": round(risk["cvar_price"], 2),
        "var_pct": round(-risk["var_pct"] * 100, 2),
        "cvar_pct": round(-risk["cvar_pct"] * 100, 2),
        "max_drawdown_pct": round(-risk["max_drawdown_pct"] * 100, 2),
        "mdd_peak_price": round(risk["mdd_peak_price"], 2),
        "mdd_trough_price": round(risk["mdd_trough_price"], 2),
        "mdd_peak_date": risk["mdd_peak_date"],
        "mdd_trough_date": risk["mdd_trough_date"],
        "confidence": confidence,
        "k_line_window_size": k_line_window_size,
        "period": period,
        "step_mode": step_mode,
        "interval": interval,
        "interval_pct": interval_pct,
        "step": round(step, 4),
        "date_start": closes_series.index[0].strftime("%Y-%m-%d"),
        "date_end": closes_series.index[-1].strftime("%Y-%m-%d"),
        "sample_days": len(close_prices),
        "price_levels": [round(float(p), 2) for p in price_levels],
        "prob_pct": prob_pct,
        "loss_prob": [round(float(x), 2) for x in risk["loss_prob"]],
    }


def generate_pnl_matrix(
    ticker: str,
    shares: int,
    commission_pct: float,
    period: str,
    confidence: float,
    price_range: int,
    interval: float = 1.0,
    interval_pct: float = 0.5,
    step_mode: str = "dollar",
    commission_mode: str = "percent",
    commission_fixed: float = 0.0,
    k_line_window_size: int = 5,
    use_closing_price: bool = True,
    hypothetical_price: float | None = None,
) -> dict[str, Any]:
    """生成損益矩陣、信心值及風險數據，回傳資料字典（不寫檔）。

    shares 僅寫入輸出供前端預設股數，不參與後端數值計算（損益在前端即時算）。
    """
    if not (MIN_PRICE_RANGE <= price_range <= MAX_PRICE_RANGE):
        raise ValueError(
            f"價位格數需介於 {MIN_PRICE_RANGE} 到 {MAX_PRICE_RANGE} 之間（目前: {price_range}）。"
        )
    if not (0.0 < confidence < 1.0):
        raise ValueError(f"VaR/CVaR 信心水準需介於 0 到 1 之間（目前: {confidence}）。")
    if k_line_window_size < MIN_K_LINE_WINDOW:
        print(f"警告：K線窗口天數過小（{k_line_window_size}），已自動調整為 {MIN_K_LINE_WINDOW}。")
        k_line_window_size = MIN_K_LINE_WINDOW

    closes_series, close_prices, k_line_window_size = _download_closes(
        ticker, period, k_line_window_size
    )
    closing_price = float(close_prices[-1])
    reference_price = _resolve_reference_price(closing_price, use_closing_price, hypothetical_price)

    step, price_levels = _build_price_grid(
        reference_price, price_range, step_mode, interval, interval_pct
    )
    print(f"收盤價: {closing_price:.2f}")
    if use_closing_price:
        print(f"參考價位: {reference_price:.2f}（收盤價）")
    else:
        print(f"參考價位: {reference_price:.2f}（假設價，收盤 {closing_price:.2f}）")
    print(f"間距模式: {step_mode} (每格 {step:.4f})")
    print(f"價格矩陣範圍: {price_levels[0]:.2f} 到 {price_levels[-1]:.2f}")

    prob_dist = _compute_confidence(
        close_prices, reference_price, price_levels, step, k_line_window_size
    )
    print("信心值計算完成。")

    risk = _compute_risk_metrics(
        closes_series, close_prices, reference_price, price_levels, confidence
    )
    print(f"VaR ({confidence*100}%) 價格: {risk['var_price']:.2f} (跌幅 {-risk['var_pct']*100:.2f}%)")
    print(f"CVaR ({confidence*100}%) 價格: {risk['cvar_price']:.2f} (跌幅 {-risk['cvar_pct']*100:.2f}%)")
    print(
        f"最大跌幅: {-risk['max_drawdown_pct']*100:.2f}% "
        f"({risk['mdd_peak_price']:.2f}@{risk['mdd_peak_date']} → "
        f"{risk['mdd_trough_price']:.2f}@{risk['mdd_trough_date']})"
    )

    return _format_output(
        ticker=ticker,
        shares=shares,
        commission_pct=commission_pct,
        commission_mode=commission_mode,
        commission_fixed=commission_fixed,
        closing_price=closing_price,
        reference_price=reference_price,
        use_closing_price=use_closing_price,
        confidence=confidence,
        k_line_window_size=k_line_window_size,
        period=period,
        step_mode=step_mode,
        interval=interval,
        interval_pct=interval_pct,
        step=step,
        closes_series=closes_series,
        close_prices=close_prices,
        price_levels=price_levels,
        prob_dist=prob_dist,
        risk=risk,
    )


def save_json(data: dict[str, Any], path: str = OUTPUT_FILE) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    print(f"\n數據已寫入 {path}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="生成股票損益矩陣資料 (matrix_data.json)")
    p.add_argument("--ticker", default="TSLA", help="股票代碼")
    p.add_argument("--shares", type=int, default=5, help="持有股數")
    p.add_argument("--commission-pct", type=float, default=0.001, help="百分比佣金率")
    p.add_argument("--commission-fixed", type=float, default=0.0, help="固定金額佣金 ($/筆)")
    p.add_argument("--commission-mode", choices=["percent", "fixed"], default="percent")
    p.add_argument("--period", default="1y", help="歷史數據期間")
    p.add_argument("--interval", type=float, default=1.0, help="固定金額模式下每格的間距 ($)")
    p.add_argument("--interval-pct", type=float, default=0.5, help="百分比模式下每格的間距 (%)")
    p.add_argument("--step-mode", choices=["dollar", "percent"], default="dollar")
    p.add_argument("--confidence", type=float, default=0.95, help="VaR/CVaR 置信水平")
    p.add_argument("--price-range", type=int, default=30, help="以參考價為中心向上下擴展的格數")
    p.add_argument("--k-line-window", type=int, default=5, help="K 線形態比對的窗口天數")
    p.add_argument("--window", type=int, help=argparse.SUPPRESS)
    p.add_argument("--hypothetical-price", type=float, default=None, help="假設價（非收盤價模式）")
    p.add_argument("--no-save", action="store_true", help="不要寫出 matrix_data.json")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    k_line_window = args.k_line_window if args.window is None else args.window
    raw = {
        "ticker": args.ticker,
        "shares": args.shares,
        "commission_pct": args.commission_pct,
        "commission_fixed": args.commission_fixed,
        "commission_mode": args.commission_mode,
        "period": args.period,
        "interval": args.interval,
        "interval_pct": args.interval_pct,
        "step_mode": args.step_mode,
        "confidence": args.confidence,
        "price_range": args.price_range,
        "k_line_window_size": k_line_window,
        "use_closing_price": args.hypothetical_price is None,
        "hypothetical_price": args.hypothetical_price,
    }
    try:
        kwargs = build_matrix_kwargs(raw, strict_reference_price=args.hypothetical_price is not None)
        data = generate_pnl_matrix(**kwargs)
        if not args.no_save:
            save_json(data)
    except Exception as e:  # noqa: BLE001
        print(f"\n錯誤：{e}")
        if not args.no_save:
            save_json({"error": str(e)})


if __name__ == "__main__":
    main()
