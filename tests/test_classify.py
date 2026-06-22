"""Tests for etf_lite.classify — the instrument classifier.

Runnable two ways:
    pytest tests/test_classify.py
    python -m tests.test_classify     # plain-assert runner, no pytest needed
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_lite.classify import (  # noqa: E402
    CASH, DERIVATIVE, EQUITY, FX, classify_instrument, is_trackable,
)


def _row(**kw):
    base = dict(
        constituent_ticker=None, constituent_name=None, isin=None, sedol=None,
        shares_held=None, market_value=None, weight_pct=None, currency=None,
        sector=None, country=None, asset_class=None,
    )
    base.update(kw)
    return base


# --- tier 1: provider asset-class label wins -------------------------------

def test_provider_equity():
    assert classify_instrument(_row(asset_class="Equity", constituent_name="Newmont",
                                    constituent_ticker="NEM", isin="US6516391066",
                                    weight_pct=11.3)) == EQUITY

def test_provider_cash_and_derivatives():
    # iShares' combined label — non-trackable either way.
    assert classify_instrument(_row(asset_class="Cash and/or Derivatives",
                                    constituent_name="USD CASH")) == CASH

def test_provider_money_market():
    assert classify_instrument(_row(asset_class="Money Market",
                                    constituent_name="BlackRock Cash Fund")) == CASH

def test_provider_futures():
    assert classify_instrument(_row(asset_class="Futures",
                                    constituent_name="COPPER FUT JUL26",
                                    market_value=120000)) == DERIVATIVE

def test_provider_label_beats_name():
    # Provider says Equity even though the name has a scary word.
    assert classify_instrument(_row(asset_class="Equity",
                                    constituent_name="FUTURE METALS NL",
                                    constituent_ticker="FME", isin="AU0000111111",
                                    weight_pct=0.4)) == EQUITY


# --- tier 2: heuristic fallback (no provider label) ------------------------

def test_fx_currency_name():
    for ccy in ("CANADIAN DOLLAR", "SAUDI RIYAL", "KOREAN WON", "JAPANESE YEN"):
        assert classify_instrument(_row(constituent_name=ccy, weight_pct=0.0)) == FX, ccy

def test_fx_currency_code_ticker():
    assert classify_instrument(_row(constituent_ticker="CAD", weight_pct=0.1)) == FX

def test_residual_payable_receivable():
    # The exact offender from the deployed alert.
    assert classify_instrument(_row(constituent_name="OTHER PAYABLE & RECEIVABLES",
                                    market_value=12345)) == CASH

def test_hedge_with_value_is_caught():
    # The gap the old zero-economic rule missed: a forward carrying real MTM.
    assert classify_instrument(_row(constituent_name="USD/CAD FX FORWARD",
                                    weight_pct=0.2, market_value=50000)) == DERIVATIVE

def test_zero_economic_future():
    # No deriv keyword in the name, but zero weight AND zero value -> index future.
    assert classify_instrument(_row(constituent_name="ROLLING CONTRACT PLACEHOLDER",
                                    constituent_ticker="ABC1", weight_pct=0,
                                    market_value=0)) == DERIVATIVE

def test_real_equity_no_label():
    assert classify_instrument(_row(constituent_name="BHP GROUP LTD",
                                    constituent_ticker="BHP", isin="AU000000BHP4",
                                    weight_pct=13.7, market_value=1e8)) == EQUITY

def test_isin_gates_fx_false_positive():
    # Ticker "EUR" is a currency code, but a real ISIN means it's a real holding
    # (European Lithium), not an FX sleeve.
    assert classify_instrument(_row(constituent_name="EUROPEAN LITHIUM LTD",
                                    constituent_ticker="EUR", isin="AU0000EUR123",
                                    weight_pct=0.5, market_value=250000)) == EQUITY


# --- the trackable wrapper -------------------------------------------------

def test_is_trackable():
    assert is_trackable(_row(constituent_name="BHP", constituent_ticker="BHP",
                             isin="AU000000BHP4", weight_pct=10)) is True
    assert is_trackable(_row(constituent_name="CANADIAN DOLLAR")) is False
    assert is_trackable(_row(constituent_name="USD/CAD FORWARD",
                             market_value=50000)) is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
    print(f"{passed}/{len(fns)} tests passed")
    sys.exit(0 if passed == len(fns) else 1)
