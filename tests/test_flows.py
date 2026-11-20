"""Traded value splits real trading from price drift; AUD conversion by fund."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_lite.cross_etf import aggregate_cross_etf  # noqa: E402
from etf_lite.flows import UsdAud, enrich, traded_value  # noqa: E402

D = date(2026, 11, 18)


def _chg(**kw):
    r = {"etf_ticker": "REMX", "delta_type": "change", "curr_as_of_date": D,
         "prev_as_of_date": date(2026, 11, 3), "isin": "AU0000000001"}
    r.update(kw)
    return r


def test_change_uses_average_price_not_value_delta():
    # The REMX / EQR case: 172.36m -> 165.18m shares, price 0.1871 -> 0.1758.
    r = _chg(prev_shares=172_362_859, curr_shares=165_182_577,
             prev_market_value=32_257_601, curr_market_value=29_034_727,
             delta_shares=165_182_577 - 172_362_859,
             delta_market_value=29_034_727 - 32_257_601)
    tv = traded_value(r)
    assert -1.40e6 < tv < -1.30e6          # ~$1.3m actually sold
    assert abs(r["delta_market_value"]) > 3.2e6  # vs $3.22m value fall


def test_addition_and_removal_are_whole_position():
    assert traded_value({"delta_type": "addition", "curr_market_value": 5e6}) == 5e6
    assert traded_value({"delta_type": "removal", "prev_market_value": 2e6}) == -2e6


def test_no_price_gives_none_not_zero():
    r = _chg(prev_shares=100, curr_shares=90, prev_market_value=0,
             curr_market_value=0, delta_shares=-10)
    assert traded_value(r) is None


def test_usd_fund_converted_aud_fund_passed_through():
    fx = UsdAud({date(2026, 11, 17): 1.50, date(2026, 11, 20): 9.99})
    usd = _chg(prev_shares=100, curr_shares=110, prev_market_value=1000,
               curr_market_value=1100, delta_shares=10, delta_market_value=100)
    aud = dict(usd, etf_ticker="MNRS")
    enrich([usd, aud], fx)
    assert usd["fx_to_aud"] == 1.50          # latest rate on/before 18 Nov
    assert abs(usd["traded_value_aud"] - 150.0) < 1e-9
    assert aud["fx_to_aud"] == 1.0 and abs(aud["traded_value_aud"] - 100.0) < 1e-9


def test_no_fx_leaves_usd_aud_fields_empty():
    r = _chg(prev_shares=100, curr_shares=110, prev_market_value=1000,
             curr_market_value=1100, delta_shares=10, delta_market_value=100)
    enrich([r], None)
    assert r["traded_value"] is not None and r["traded_value_aud"] is None


def test_cross_etf_sums_traded_aud():
    rows = [
        {"etf_ticker": "A", "isin": "X1", "delta_type": "change", "delta_weight_pct": 0.1,
         "delta_shares": 1, "delta_market_value": 5.0, "traded_value_aud": 3.0},
        {"etf_ticker": "B", "isin": "X1", "delta_type": "change", "delta_weight_pct": 0.1,
         "delta_shares": 1, "delta_market_value": 5.0, "traded_value_aud": 4.0},
    ]
    (sig,) = aggregate_cross_etf(rows)
    assert sig["total_traded_value_aud"] == 7.0
