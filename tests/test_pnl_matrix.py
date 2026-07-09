"""Tests for pnl_matrix core logic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from params import coerce_reference_price_params
from pnl_matrix import (
    _compute_loss_prob,
    _resolve_reference_price,
    generate_pnl_matrix,
)


class TestResolveReferencePrice:
    def test_closing_price(self):
        assert _resolve_reference_price(100.0, True, None) == 100.0
        assert _resolve_reference_price(100.0, True, 50.0) == 100.0

    def test_hypothetical_price(self):
        assert _resolve_reference_price(100.0, False, 105.5) == 105.5

    def test_invalid_hypothetical(self):
        with pytest.raises(ValueError, match="假設價"):
            _resolve_reference_price(100.0, False, None)
        with pytest.raises(ValueError, match="假設價"):
            _resolve_reference_price(100.0, False, 0)
        with pytest.raises(ValueError, match="假設價"):
            _resolve_reference_price(100.0, False, -1.0)


class TestCoerceReferencePriceParams:
    def test_closing(self):
        assert coerce_reference_price_params(True, 50.0) == (True, None)

    def test_valid_hypo(self):
        assert coerce_reference_price_params(False, 105.0) == (False, 105.0)

    def test_invalid_hypo_falls_back(self):
        assert coerce_reference_price_params(False, None) == (True, None)
        assert coerce_reference_price_params(False, 0) == (True, None)


class TestLossProbVectorized:
    def test_matches_loop(self):
        returns_arr = np.array([0.01, -0.02, 0.005, -0.03, 0.0])
        price_levels = np.array([90.0, 95.0, 100.0, 105.0])
        reference = 100.0
        level_returns = price_levels / reference - 1.0
        loop = np.array([float(np.mean(returns_arr < lr)) for lr in level_returns]) * 100.0
        vec = _compute_loss_prob(returns_arr, price_levels, reference)
        np.testing.assert_allclose(loop, vec)


def _mock_hist(n: int = 60, start: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    closes = start + np.linspace(-5, 5, n) + np.sin(np.linspace(0, 4, n)) * 2
    return pd.DataFrame({"Close": closes}, index=dates)


@patch("pnl_matrix.yf.download")
def test_output_has_prob_pct_not_matrix(mock_download: MagicMock):
    mock_download.return_value = _mock_hist()
    data = generate_pnl_matrix(
        ticker="TEST",
        shares=5,
        commission_pct=0.001,
        period="1y",
        confidence=0.95,
        price_range=5,
    )
    assert "prob_pct" in data
    assert "matrix" not in data
    assert len(data["prob_pct"]) == len(data["price_levels"])


@patch("pnl_matrix.yf.download")
def test_compute_risk_metrics_keys(mock_download: MagicMock):
    mock_download.return_value = _mock_hist()
    data = generate_pnl_matrix(
        ticker="TEST",
        shares=5,
        commission_pct=0.001,
        period="1y",
        confidence=0.95,
        price_range=5,
    )
    for key in (
        "var_price", "cvar_price", "var_pct", "cvar_pct",
        "loss_prob", "max_drawdown_pct", "mdd_peak_price", "mdd_trough_price",
        "mdd_peak_date", "mdd_trough_date",
    ):
        assert key in data
    assert len(data["loss_prob"]) == len(data["price_levels"])


@patch("pnl_matrix.yf.download")
def test_reference_price_in_output(mock_download: MagicMock):
    mock_download.return_value = _mock_hist()
    data = generate_pnl_matrix(
        ticker="TEST",
        shares=5,
        commission_pct=0.001,
        period="1y",
        confidence=0.95,
        price_range=5,
        use_closing_price=False,
        hypothetical_price=120.0,
    )
    assert "reference_price" in data
    assert "closing_price" in data
    assert "last_price" not in data
    assert data["reference_price"] == 120.0
    assert data["use_closing_price"] is False
    assert data["closing_price"] != data["reference_price"]
