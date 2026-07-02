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
         - get_data()        初次載入，以上次關閉前/預設參數即時計算
         - regenerate(params) 以新參數重新下載並計算
         - save_client_settings(params) 只記住股數/佣金等前端即時設定，不觸發重新計算
       矩陣資料只存在記憶體中，回傳給前端渲染，不寫任何 .json 檔；
       但「使用者上次的設定」會存成一個小 JSON，下次開啟自動帶入（見 _settings_path()）。
"""

from __future__ import annotations

import json
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


def _settings_path() -> str:
    """設定檔存放路徑：優先用系統的使用者資料夾，確保打包成 --onefile exe 後
    （執行檔會解壓到暫存資料夾）依然能跨次執行持久化，而不是寫進即用即刪的暫存目錄。
    """
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~")
    settings_dir = os.path.join(base, "PnLMatrix")
    try:
        os.makedirs(settings_dir, exist_ok=True)
    except OSError:
        settings_dir = BASE_DIR  # 極端狀況（無寫入權限）退回程式目錄，至少當次可用
    return os.path.join(settings_dir, "settings.json")


def _load_settings() -> dict:
    """讀取上次儲存的使用者設定；檔案不存在、損毀或欄位不明都安全退回預設值。"""
    merged = dict(DEFAULT_PARAMS)
    try:
        with open(_settings_path(), "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            merged.update({k: v for k, v in saved.items() if k in DEFAULT_PARAMS})
    except (OSError, ValueError):
        pass  # 首次執行沒有設定檔，或檔案壞掉——都用預設值即可
    return merged


def _save_settings(params: dict) -> None:
    """儲存目前設定，僅保留已知欄位，避免寫入未預期的雜訊。失敗不影響主流程。"""
    try:
        payload = {k: params[k] for k in DEFAULT_PARAMS if k in params}
        with open(_settings_path(), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"設定儲存失敗（不影響使用）: {e}")


class Api:
    """暴露給前端 JS 的方法。資料只存在記憶體中，不寫任何 .json 檔。

    前端用法:
        await window.pywebview.api.get_data()          // 初次載入（用上次/預設參數）
        await window.pywebview.api.regenerate(params)   // 以新參數重新計算
    """

    def __init__(self) -> None:
        self._params = _load_settings()
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
        """以指定參數重新下載並計算，直接回傳完整資料（矩陣本身不寫檔，但設定會存檔以便下次開啟沿用）。"""
        merged = dict(self._params)
        merged.update(params or {})
        try:
            data = self._compute(merged)
            self._params = merged
            self._cache = data
            _save_settings(merged)
            return data
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

    def save_client_settings(self, params: dict | None = None) -> dict:
        """只記住前端即時調整的設定（如股數、佣金），不重新下載/計算。
        用於使用者拖動股數/佣金輸入框時，讓下次開啟也能沿用，而不必按「重新計算」。
        """
        merged = dict(self._params)
        merged.update(params or {})
        self._params = merged
        _save_settings(merged)
        return {"ok": True}


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
