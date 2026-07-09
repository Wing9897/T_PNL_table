"""Tests for build_matrix_kwargs and params constants."""

from __future__ import annotations

from params import DEFAULT_PARAMS, MAX_PRICE_RANGE, MIN_PRICE_RANGE, build_matrix_kwargs, coerce_reference_price_params


def test_build_matrix_kwargs_defaults():
    kw = build_matrix_kwargs({})
    assert kw["ticker"] == "TSLA"
    assert kw["shares"] == 5
    assert kw["price_range"] == 30
    assert kw["use_closing_price"] is True
    assert kw["hypothetical_price"] is None


def test_build_matrix_kwargs_type_coercion():
    kw = build_matrix_kwargs({
        "ticker": "msft",
        "shares": "10",
        "confidence": "0.9",
        "price_range": "999",
    })
    assert kw["ticker"] == "MSFT"
    assert kw["shares"] == 10
    assert kw["confidence"] == 0.9
    assert kw["price_range"] == MAX_PRICE_RANGE


def test_build_matrix_kwargs_clamps_price_range():
    kw = build_matrix_kwargs({"price_range": 0})
    assert kw["price_range"] == MIN_PRICE_RANGE


def test_build_matrix_kwargs_strict_invalid_hypo():
    kw = build_matrix_kwargs(
        {"use_closing_price": False, "hypothetical_price": -1},
        strict_reference_price=True,
    )
    assert kw["use_closing_price"] is False
    assert kw["hypothetical_price"] == -1.0


def test_build_matrix_kwargs_coerce_invalid_hypo():
    kw = build_matrix_kwargs(
        {"use_closing_price": False, "hypothetical_price": 0},
        strict_reference_price=False,
    )
    assert kw["use_closing_price"] is True
    assert kw["hypothetical_price"] is None


def test_coerce_reference_price_params():
    assert coerce_reference_price_params(True, 50.0) == (True, None)
    assert coerce_reference_price_params(False, 105.0) == (False, 105.0)
    assert coerce_reference_price_params(False, None) == (True, None)


def test_settings_keys_includes_hypothetical():
    from params import SETTINGS_KEYS

    assert "hypothetical_price" in SETTINGS_KEYS
    assert DEFAULT_PARAMS.keys() <= SETTINGS_KEYS
