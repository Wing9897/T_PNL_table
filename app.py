"""
PnL Matrix 桌面 App
===================
用 pywebview 包裝 pnl_matrix.html，計算結果直接透過 JS API 回傳前端（不寫 .json）。

用法:
    python app.py
"""

from __future__ import annotations

import json
import os
import sys

import webview

from params import DEFAULT_PARAMS, SETTINGS_KEYS, build_matrix_kwargs
from pnl_matrix import generate_pnl_matrix


def _resource_dir() -> str:
    if getattr(sys, "frozen", False):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _resource_dir()
HTML_FILE = os.path.join(BASE_DIR, "pnl_matrix.html")


def _settings_path() -> str:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~")
    settings_dir = os.path.join(base, "PnLMatrix")
    try:
        os.makedirs(settings_dir, exist_ok=True)
    except OSError:
        settings_dir = BASE_DIR
    return os.path.join(settings_dir, "settings.json")


def normalize_params(raw: dict) -> dict:
    """設定載入用：無效假設價靜默退回收盤價。"""
    return build_matrix_kwargs(raw, strict_reference_price=False)


def _migrate_settings(data: dict) -> dict:
    if "window" in data and "k_line_window_size" not in data:
        data["k_line_window_size"] = data.pop("window")
    if data.get("use_closing_price", True):
        data.pop("hypothetical_price", None)
    return data


def _load_settings() -> dict:
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return _migrate_settings(data)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_settings(params: dict) -> None:
    try:
        to_save = {k: params[k] for k in SETTINGS_KEYS if k in params}
        with open(_settings_path(), "w", encoding="utf-8") as f:
            json.dump(to_save, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"設定儲存失敗（不影響使用）: {e}")


class Api:
    def __init__(self) -> None:
        saved = _load_settings()
        self._params = normalize_params({**DEFAULT_PARAMS, **saved})
        self._cache: dict | None = None

    def get_data(self) -> dict:
        if self._cache is None:
            try:
                self._cache = generate_pnl_matrix(**self._params)
            except Exception as e:  # noqa: BLE001
                return {"error": str(e)}
        return self._cache

    def regenerate(self, params: dict | None = None) -> dict:
        merged = {**self._params, **(params or {})}
        try:
            kwargs = build_matrix_kwargs(merged, strict_reference_price=True)
            self._params = kwargs
            self._cache = generate_pnl_matrix(**kwargs)
            _save_settings(self._params)
            return self._cache
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

    def save_client_settings(self, params: dict | None = None) -> dict:
        merged = {**self._params, **(params or {})}
        self._params = normalize_params(merged)
        _save_settings(self._params)
        return {"ok": True}


def main() -> None:
    api = Api()
    window = webview.create_window(
        "PnL Matrix",
        HTML_FILE,
        js_api=api,
        width=1400,
        height=900,
        min_size=(900, 600),
    )
    window.events.closed += lambda: os._exit(0)
    webview.start(debug=bool(os.environ.get("PNL_DEBUG")))
    os._exit(0)


if __name__ == "__main__":
    main()
