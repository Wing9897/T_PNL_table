"""Tests for app resource paths and smoke checks."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from app import Api, DEFAULT_PARAMS, HTML_FILE, _resource_dir, normalize_params
from params import SETTINGS_KEYS


def test_resource_dir_unfrozen():
    path = _resource_dir()
    assert Path(path).is_dir()
    assert Path(HTML_FILE).exists()


def test_resource_dir_frozen_uses_meipass(monkeypatch, tmp_path):
    fake_meipass = tmp_path / "bundle"
    fake_meipass.mkdir()
    (fake_meipass / "pnl_matrix.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_meipass), raising=False)
    assert _resource_dir() == str(fake_meipass)


def test_frozen_bundle_has_static_assets(monkeypatch, tmp_path):
    fake_meipass = tmp_path / "bundle"
    fake_meipass.mkdir()
    for name in ("pnl_matrix.html", "pnl_matrix.css", "pnl_matrix.js"):
        (fake_meipass / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_meipass), raising=False)
    base = Path(_resource_dir())
    for name in ("pnl_matrix.html", "pnl_matrix.css", "pnl_matrix.js"):
        assert (base / name).exists()


def _mock_hist(n: int = 60) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    return pd.DataFrame({"Close": 100 + np.linspace(-2, 2, n)}, index=dates)


@patch("pnl_matrix.yf.download")
def test_manual_smoke_api_flow(mock_download):
    """原 run_manual_checks.py 的桌面 API 流程（mock yfinance）。"""
    mock_download.return_value = _mock_hist()
    api = Api()
    api._params = dict(DEFAULT_PARAMS)
    api._cache = None

    d = api.get_data()
    assert "error" not in d
    assert d["reference_price"] == d["closing_price"]

    d2 = api.regenerate({"use_closing_price": False, "hypothetical_price": 110.0, "price_range": 5})
    assert d2["reference_price"] == 110.0

    api.save_client_settings({"shares": 20})
    assert api._params["shares"] == 20

    d3 = api.regenerate({"use_closing_price": True})
    assert d3["reference_price"] == d3["closing_price"]


def test_static_files_exist():
    root = Path(__file__).resolve().parents[1]
    for name in ("pnl_matrix.html", "pnl_matrix.css", "pnl_matrix.js"):
        assert (root / name).exists()


def test_html_references_css_js():
    root = Path(__file__).resolve().parents[1]
    html = (root / "pnl_matrix.html").read_text(encoding="utf-8")
    assert 'href="pnl_matrix.css"' in html
    assert 'src="pnl_matrix.js"' in html


def test_normalize_params_invalid_hypo_falls_back():
    kw = normalize_params({"use_closing_price": False, "hypothetical_price": 0})
    assert kw["use_closing_price"] is True


def test_settings_keys_used():
    assert "hypothetical_price" in SETTINGS_KEYS
