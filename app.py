"""
PnL Matrix 桌面端
=================
用 pywebview 把現有的 HTML/JS 前端包成原生桌面視窗，後端沿用 pnl_matrix.py 計算。

執行前安裝相依套件:
    pip install pywebview

執行:
    python app.py

打包成單一 .exe (Windows):
    pip install pyinstaller
    pyinstaller --noconfirm --windowed --name PnLMatrix ^
        --add-data "pnl_matrix.html;." app.py

運作方式:
    1. 開啟一個 pywebview 原生視窗，直接載入 pnl_matrix.html。
    2. 透過 js_api（Api 類別）讓前端呼叫 Python 計算資料：
         - get_data()        初次載入，以預設/上次參數即時計算
         - regenerate(params) 以新參數重新下載並計算
       資料只存在記憶體中，回傳給前端渲染，不寫任何 .json 檔。
"""

from __future__ import annotations

import os

import webview  # pip install pywebview

from pnl_matrix import generate_pnl_matrix

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_FILE = os.path.join(BASE_DIR, "pnl_matrix.html")


DEFAULT_PARAMS: dict = {
    "ticker": "AAPL",
    "shares": 5,
    "commission_pct": 0.001,
    "period": "1y",
    "confidence": 0.95,
    "price_range": 18,
    "interval": 1.0,
    "interval_pct": 0.5,
    "step_mode": "dollar",
    "commission_mode": "percent",
    "commission_fixed": 0.0,
    "window": 5,
}


class Api:
    """暴露給前端 JS 的方法。資料只存在記憶體中，不寫任何 .json 檔。

    前端用法:
        await window.pywebview.api.get_data()          // 初次載入（用上次/預設參數）
        await window.pywebview.api.regenerate(params)   // 以新參數重新計算
    """

    def __init__(self) -> None:
        self._params = dict(DEFAULT_PARAMS)
        self._cache: dict | None = None

    def _compute(self, params: dict) -> dict:
        return generate_pnl_matrix(
            ticker=str(params.get("ticker", "AAPL")).upper(),
            shares=int(params.get("shares", 5)),
            commission_pct=float(params.get("commission_pct", 0.001)),
            period=str(params.get("period", "1y")),
            confidence=float(params.get("confidence", 0.95)),
            price_range=int(params.get("price_range", 18)),
            interval=float(params.get("interval", 1.0)),
            interval_pct=float(params.get("interval_pct", 0.5)),
            step_mode=str(params.get("step_mode", "dollar")),
            commission_mode=str(params.get("commission_mode", "percent")),
            commission_fixed=float(params.get("commission_fixed", 0.0)),
            k_line_window_size=int(params.get("window", 5)),
        )

    def get_data(self) -> dict:
        """回傳目前資料；首次呼叫時以預設參數即時計算並快取。"""
        try:
            if self._cache is None:
                self._cache = self._compute(self._params)
            return self._cache
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

    def regenerate(self, params: dict | None = None) -> dict:
        """以指定參數重新下載並計算，直接回傳完整資料（不寫檔）。"""
        merged = dict(self._params)
        merged.update(params or {})
        try:
            data = self._compute(merged)
            self._params = merged
            self._cache = data
            return data
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}


def main() -> None:
    window = webview.create_window(
        "PnL Matrix",
        HTML_FILE,
        js_api=Api(),
        width=1100,
        height=720,
        min_size=(820, 560),
    )
    # 視窗關閉時立即結束程序，避免 WebView/背景執行緒清理造成殘留(需手動 Ctrl+C)
    window.events.closed += lambda: os._exit(0)
    # 設環境變數 PNL_DEBUG=1 可開啟 DevTools（右鍵→Inspect）以檢視 console 錯誤
    webview.start(debug=bool(os.environ.get("PNL_DEBUG")))
    os._exit(0)  # start() 返回後保險再結束一次


if __name__ == "__main__":
    main()
