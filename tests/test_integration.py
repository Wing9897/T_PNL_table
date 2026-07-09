"""Integration tests: app API, settings, CLI params (no GUI)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app import Api, DEFAULT_PARAMS, _load_settings, _migrate_settings, _save_settings, normalize_params
from pnl_matrix import generate_pnl_matrix


def _mock_hist(n: int = 60) -> pd.DataFrame:
    import numpy as np

    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    closes = 100 + np.linspace(-3, 3, n)
    return pd.DataFrame({"Close": closes}, index=dates)


@patch("pnl_matrix.yf.download")
def test_api_regenerate_closing_price(mock_download: MagicMock):
    mock_download.return_value = _mock_hist()
    api = Api()
    api._params = dict(DEFAULT_PARAMS)
    api._cache = None

    result = api.regenerate({"ticker": "TEST", "price_range": 5, "use_closing_price": True})
    assert "error" not in result
    assert result["use_closing_price"] is True
    assert result["reference_price"] == result["closing_price"]
    assert len(result["prob_pct"]) == len(result["price_levels"])
    assert "matrix" not in result


@patch("pnl_matrix.yf.download")
def test_api_regenerate_hypothetical_price(mock_download: MagicMock):
    mock_download.return_value = _mock_hist()
    api = Api()
    api._params = dict(DEFAULT_PARAMS)
    api._cache = None

    result = api.regenerate({
        "ticker": "TEST",
        "price_range": 5,
        "use_closing_price": False,
        "hypothetical_price": 115.0,
    })
    assert "error" not in result
    assert result["reference_price"] == 115.0
    assert result["closing_price"] != 115.0


@patch("pnl_matrix.yf.download")
def test_api_invalid_hypothetical_returns_error(mock_download: MagicMock):
    """regenerate 明確指定假設價但無效時回傳 error（不 silent fallback）。"""
    mock_download.return_value = _mock_hist()
    api = Api()
    result = api.regenerate({
        "use_closing_price": False,
        "hypothetical_price": -1,
    })
    assert "error" in result
    assert "假設價" in result["error"]


def test_settings_migration_preserves_hypothetical():
    saved = {"window": 7, "use_closing_price": False, "hypothetical_price": 120.0}
    migrated = _migrate_settings(saved)
    assert migrated["k_line_window_size"] == 7
    assert "window" not in migrated
    assert migrated["hypothetical_price"] == 120.0


def test_normalize_params_invalid_hypo_falls_back():
    kw = normalize_params({"use_closing_price": False, "hypothetical_price": 0})
    assert kw["use_closing_price"] is True
    assert kw["hypothetical_price"] is None


@patch("pnl_matrix.yf.download")
def test_shares_change_only_affects_frontend_path(mock_download: MagicMock):
    """Backend output shares is metadata; PnL is computed client-side."""
    mock_download.return_value = _mock_hist()
    d1 = generate_pnl_matrix(
        ticker="T", shares=5, commission_pct=0.001, period="1y",
        confidence=0.95, price_range=3,
    )
    d2 = generate_pnl_matrix(
        ticker="T", shares=100, commission_pct=0.001, period="1y",
        confidence=0.95, price_range=3,
    )
    assert d1["prob_pct"] == d2["prob_pct"]
    assert d1["price_levels"] == d2["price_levels"]
    assert d1["shares"] == 5
    assert d2["shares"] == 100
