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
from sklearn.metrics.pairwise import cosine_similarity

OUTPUT_FILE = "matrix_data.json"


def _extract_close_series(hist: pd.DataFrame, ticker: str) -> pd.Series:
    """從 yfinance 回傳的 DataFrame 中穩健地取出收盤價 Series。

    新版 yfinance 在單一 ticker 時也可能回傳 MultiIndex 欄位
    (例如 ('Close', 'AAPL'))，此處同時相容平面與多層欄位。
    """
    if isinstance(hist.columns, pd.MultiIndex):
        close = hist["Close"]
        # close 可能是 DataFrame(多 ticker) 或 Series
        if isinstance(close, pd.DataFrame):
            close = close[ticker] if ticker in close.columns else close.iloc[:, 0]
    else:
        close = hist["Close"]
    return pd.to_numeric(close, errors="coerce").dropna()


def _compute_confidence(
    closes: np.ndarray,
    last_price: float,
    price_levels: np.ndarray,
    interval: float,
    window: int,
) -> np.ndarray:
    """以最近 `window` 天 K 線形態，對歷史相似形態做餘弦相似度加權，
    估算下一日股價落在各價格水平的機率分佈。回傳長度同 price_levels 的機率陣列。
    """
    patterns = []
    outcomes = []
    for i in range(len(closes) - window):
        win = closes[i : i + window]
        patterns.append(win - win[0])               # 標準化形狀（起點歸零）
        outcomes.append(closes[i + window] - win[-1])  # 後一日的價格變化量

    patterns_arr = np.asarray(patterns)
    outcomes_arr = np.asarray(outcomes)

    target = (closes[-window:] - closes[-window]).reshape(1, -1)
    similarities = cosine_similarity(patterns_arr, target).flatten()
    similarities = np.nan_to_num(similarities)  # 平盤(零向量)時餘弦可能為 NaN，歸零

    weights = (similarities + 1.0) / 2.0 + 1e-9      # 映射到非負權重
    projected = last_price + outcomes_arr            # 投射到當前價位

    bins = np.append(price_levels, price_levels[-1] + interval)
    prob_dist, _ = np.histogram(projected, bins=bins, weights=weights)

    total = prob_dist.sum()
    if total > 0:
        return prob_dist / total
    return np.ones(len(price_levels)) / len(price_levels)  # 退回均勻分佈


def _build_matrix(
    price_levels: np.ndarray,
    shares: int,
    commission_mode: str,
    commission_value: float,
    prob_dist: np.ndarray,
) -> list[list[dict[str, Any]]]:
    """向量化計算損益矩陣，回傳前端所需的巢狀 dict 結構。

    commission_mode:
        "percent" → commission_value 為交易金額比率（如 0.001 = 0.1%），每邊各收。
        "fixed"   → commission_value 為每筆固定金額（如 1.0 元），買/賣各收一次。
    """
    if commission_mode == "fixed":
        buy_cost = price_levels * shares + commission_value          # (n,)
        sell_rev = price_levels * shares - commission_value          # (n,)
    else:
        buy_cost = price_levels * shares * (1 + commission_value)    # (n,)
        sell_rev = price_levels * shares * (1 - commission_value)    # (n,)
    pnl = sell_rev[None, :] - buy_cost[:, None]                      # (n, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pnl_pct = np.where(buy_cost[:, None] != 0, pnl / buy_cost[:, None] * 100, 0.0)

    prob_pct = np.round(prob_dist * 100, 2)
    n = len(price_levels)

    matrix: list[list[dict[str, Any]]] = []
    for i in range(n):
        row = [
            {
                "buy": round(float(price_levels[i]), 2),
                "sell": round(float(price_levels[j]), 2),
                "pnl": round(float(pnl[i, j]), 2),
                "pnl_pct": round(float(pnl_pct[i, j]), 2),
                "prob": float(prob_pct[j]),
            }
            for j in range(n)
        ]
        matrix.append(row)
    return matrix


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
) -> dict[str, Any]:
    """生成損益矩陣、信心值及風險數據，回傳資料字典（不寫檔）。

    step_mode:
        "dollar"  → 每格間距為固定金額 interval（例如 $1）。
        "percent" → 每格間距為現價的 interval_pct%（例如 0.5%）。
    commission_mode:
        "percent" → 佣金為交易金額的 commission_pct（如 0.001 = 0.1%），每邊各收。
        "fixed"   → 佣金為每筆固定金額 commission_fixed（如 $1），買/賣各收一次。
    """
    print(f"開始為 {ticker} 生成數據...")

    hist = yf.download(ticker, period=period, progress=False, auto_adjust=True)
    if hist is None or hist.empty:
        raise ValueError(f"無法下載 {ticker} 的數據，請檢查 ticker 是否正確。")

    closes_series = _extract_close_series(hist, ticker)
    close_prices = closes_series.to_numpy(dtype=float)

    # 數據充足性檢查與回退
    if len(close_prices) < 30:
        print(f"警告：歷史數據少於30天（僅 {len(close_prices)} 天），K線窗口將縮小。")
        k_line_window_size = min(k_line_window_size, max(1, len(close_prices) - 3))
    if len(close_prices) < k_line_window_size + 2:
        raise ValueError(
            f"數據不足（需要至少 {k_line_window_size + 2} 天，"
            f"但只有 {len(close_prices)} 天）無法進行計算。"
        )

    last_price = float(close_prices[-1])
    # 依模式決定每格間距：固定金額，或現價的百分比
    step = last_price * (interval_pct / 100.0) if step_mode == "percent" else interval
    if step <= 0:
        raise ValueError("價格間距必須大於 0。")
    price_levels = np.round(
        np.arange(
            last_price - price_range * step,
            last_price + (price_range + 1) * step,
            step,
        ),
        2,
    )
    print(f"最新股價: {last_price:.2f}")
    print(f"間距模式: {step_mode} (每格 {step:.4f})")
    print(f"價格矩陣範圍: {price_levels[0]:.2f} 到 {price_levels[-1]:.2f}")

    prob_dist = _compute_confidence(
        close_prices, last_price, price_levels, step, k_line_window_size
    )
    print("信心值計算完成。")

    commission_value = commission_fixed if commission_mode == "fixed" else commission_pct
    matrix = _build_matrix(price_levels, shares, commission_mode, commission_value, prob_dist)
    print("損益矩陣計算完成。")

    # VaR / CVaR
    returns = closes_series.pct_change().dropna()
    var_pct = float(returns.quantile(1 - confidence))
    tail = returns[returns <= var_pct]
    cvar_pct = float(tail.mean()) if not tail.empty else var_pct * 1.5

    var_price = last_price * (1 + var_pct)
    cvar_price = last_price * (1 + cvar_pct)
    print(f"VaR ({confidence*100}%) 價格: {var_price:.2f} (跌幅 {-var_pct*100:.2f}%)")
    print(f"CVaR ({confidence*100}%) 價格: {cvar_price:.2f} (跌幅 {-cvar_pct*100:.2f}%)")

    # 每個買入價的「蝕錢機率」：用同一份日報酬經驗分佈，估 P(下一交易日收盤 < 該價)
    # 語意：在此價買入後，隔日仍低於此價（浮虧）的機率。VaR 價位該列≈ (1-confidence)。
    returns_arr = returns.to_numpy(dtype=float)
    level_returns = price_levels / last_price - 1.0
    loss_prob = np.array([float(np.mean(returns_arr < lr)) for lr in level_returns]) * 100.0

    # 最大跌幅 (Maximum Drawdown)：期間內由高點到其後低點的最大跌幅
    running_max = np.maximum.accumulate(close_prices)
    drawdowns = close_prices / running_max - 1.0
    trough_idx = int(np.argmin(drawdowns))
    peak_idx = int(np.argmax(close_prices[: trough_idx + 1])) if trough_idx > 0 else 0
    mdd_pct = float(drawdowns[trough_idx])  # 最負值
    idx = closes_series.index
    mdd_peak_date = idx[peak_idx].strftime("%Y-%m-%d")
    mdd_trough_date = idx[trough_idx].strftime("%Y-%m-%d")
    print(
        f"最大跌幅: {-mdd_pct*100:.2f}% "
        f"({close_prices[peak_idx]:.2f}@{mdd_peak_date} → {close_prices[trough_idx]:.2f}@{mdd_trough_date})"
    )

    output_data: dict[str, Any] = {
        "ticker": ticker,
        "shares": shares,
        "commission_pct": commission_pct,
        "commission_mode": commission_mode,
        "commission_value": round(float(commission_value), 6),
        "last_price": round(last_price, 2),
        "var_price": round(var_price, 2),
        "cvar_price": round(cvar_price, 2),
        "var_pct": round(-var_pct * 100, 2),
        "cvar_pct": round(-cvar_pct * 100, 2),
        "max_drawdown_pct": round(-mdd_pct * 100, 2),
        "mdd_peak_price": round(float(close_prices[peak_idx]), 2),
        "mdd_trough_price": round(float(close_prices[trough_idx]), 2),
        "mdd_peak_date": mdd_peak_date,
        "mdd_trough_date": mdd_trough_date,
        "confidence": confidence,
        "period": period,
        "step_mode": step_mode,
        "interval": interval,
        "interval_pct": interval_pct,
        "step": round(float(step), 4),
        "date_start": closes_series.index[0].strftime("%Y-%m-%d"),
        "date_end": closes_series.index[-1].strftime("%Y-%m-%d"),
        "sample_days": len(close_prices),
        "price_levels": [round(float(p), 2) for p in price_levels],
        "loss_prob": [round(float(x), 2) for x in loss_prob],
        "matrix": matrix,
    }
    return output_data


def save_json(data: dict[str, Any], path: str = OUTPUT_FILE) -> None:
    """選擇性將資料寫成 JSON（桌面 app 不需要，CLI 可用於除錯/瀏覽器模式）。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    print(f"\n數據已寫入 {path}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="生成股票損益矩陣資料 (matrix_data.json)")
    p.add_argument("--ticker", default="AAPL", help="股票代碼")
    p.add_argument("--shares", type=int, default=5, help="持有股數")
    p.add_argument("--commission-pct", type=float, default=0.001, help="百分比佣金率 (例如 0.001 = 0.1%%)，每邊各收")
    p.add_argument("--commission-fixed", type=float, default=0.0, help="固定金額佣金 ($/筆)，買/賣各收一次")
    p.add_argument("--commission-mode", choices=["percent", "fixed"], default="percent", help="佣金模式: percent=百分比, fixed=固定金額")
    p.add_argument("--period", default="1y", help="歷史數據期間 (1mo, 6mo, 1y, 5y, max)")
    p.add_argument("--interval", type=float, default=1.0, help="固定金額模式下每格的間距 ($)")
    p.add_argument("--interval-pct", type=float, default=0.5, help="百分比模式下每格的間距 (現價的 %%)")
    p.add_argument("--step-mode", choices=["dollar", "percent"], default="dollar", help="間距模式: dollar=固定金額, percent=現價百分比")
    p.add_argument("--confidence", type=float, default=0.95, help="VaR/CVaR 置信水平")
    p.add_argument("--price-range", type=int, default=18, help="以現價為中心向上下擴展的格數 (產生 (2N+1)x(2N+1) 矩陣)")
    p.add_argument("--window", type=int, default=5, help="K 線形態比對的窗口天數")
    p.add_argument("--no-save", action="store_true", help="不要寫出 matrix_data.json")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    try:
        data = generate_pnl_matrix(
            ticker=args.ticker,
            shares=args.shares,
            commission_pct=args.commission_pct,
            period=args.period,
            confidence=args.confidence,
            interval=args.interval,
            interval_pct=args.interval_pct,
            step_mode=args.step_mode,
            commission_mode=args.commission_mode,
            commission_fixed=args.commission_fixed,
            price_range=args.price_range,
            k_line_window_size=args.window,
        )
        if not args.no_save:
            save_json(data)
    except Exception as e:  # noqa: BLE001 - 對使用者輸出友善錯誤並寫入前端可讀的錯誤檔
        print(f"\n錯誤：{e}")
        if not args.no_save:
            save_json({"error": str(e)})


if __name__ == "__main__":
    main()
