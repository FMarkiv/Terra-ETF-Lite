"""Tests for cross-ETF consensus aggregation (lite), focused on the
cross-listing dedup: cross-listings of the same fund/index count as ONE
converging vote, while genuinely distinct indices stay independent.

Runnable two ways:
    pytest tests/test_cross_etf.py
    python -m tests.test_cross_etf     # plain-assert runner, no pytest needed
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_lite.cross_etf import aggregate_cross_etf, converging_count  # noqa: E402

# The equivalence groups (same as config/thresholds.yaml). The .L tickers are
# absent from the lite universe but harmless to list.
EQUIV_GROUPS = [
    ["GDX", "GDX.L", "GDX-ASX"],
    ["GDXJ", "GDXJ.L"],
    ["COPX", "4COP"],
]


def _delta(etf, isin, *, weight=0.0, delta_type="change", name="Newmont", mv=1000.0):
    return {
        "etf_ticker": etf,
        "isin": isin,
        "constituent_ticker": "NEM",
        "constituent_name": name,
        "delta_type": delta_type,
        "delta_weight_pct": weight,
        "delta_shares": 100.0,
        "delta_market_value": mv,
    }


def _by_isin(signals, isin):
    return next(s for s in signals if s["isin"] == isin)


def test_crosslisting_collapses_to_one_vote():
    deltas = [
        _delta("GDX", "US6516391066", weight=0.5),
        _delta("GDX-ASX", "US6516391066", weight=0.4),
    ]
    sig = _by_isin(aggregate_cross_etf(deltas, equiv_groups=EQUIV_GROUPS), "US6516391066")
    assert sig["n_etfs"] == 1
    assert sig["n_etfs_weight_up"] == 1
    assert converging_count(sig) == 1
    assert len(sig["etf_details"]) == 1
    assert sig["etf_details"][0]["etf_ticker"] == "GDX"


def test_distinct_indices_stay_independent():
    deltas = [
        _delta("GDX", "US6516391066", weight=0.5),
        _delta("RING", "US6516391066", weight=0.3),
    ]
    sig = _by_isin(aggregate_cross_etf(deltas, equiv_groups=EQUIV_GROUPS), "US6516391066")
    assert sig["n_etfs"] == 2
    assert sig["n_etfs_weight_up"] == 2
    assert {d["etf_ticker"] for d in sig["etf_details"]} == {"GDX", "RING"}


def test_representative_is_first_present_in_group():
    deltas = [_delta("GDX-ASX", "US6516391066", weight=0.4)]
    sig = _by_isin(aggregate_cross_etf(deltas, equiv_groups=EQUIV_GROUPS), "US6516391066")
    assert sig["n_etfs"] == 1
    assert sig["etf_details"][0]["etf_ticker"] == "GDX-ASX"


def test_copper_group():
    deltas = [
        _delta("COPX", "CU0000000001", weight=0.5, name="Freeport"),
        _delta("4COP", "CU0000000001", weight=0.4, name="Freeport"),
    ]
    sig = _by_isin(aggregate_cross_etf(deltas, equiv_groups=EQUIV_GROUPS), "CU0000000001")
    assert sig["n_etfs"] == 1


def test_no_groups_means_no_collapse():
    deltas = [
        _delta("GDX", "US6516391066", weight=0.5),
        _delta("GDX-ASX", "US6516391066", weight=0.4),
    ]
    sig = _by_isin(aggregate_cross_etf(deltas), "US6516391066")
    assert sig["n_etfs"] == 2


def test_threshold_crosslisting_fails_consensus():
    min_converging = 2
    crosslisted = aggregate_cross_etf(
        [_delta("GDX", "US6516391066", weight=0.5), _delta("GDX-ASX", "US6516391066", weight=0.4)],
        equiv_groups=EQUIV_GROUPS,
    )
    assert all(converging_count(s) < min_converging for s in crosslisted)

    distinct = aggregate_cross_etf(
        [_delta("GDX", "US6516391066", weight=0.5), _delta("RING", "US6516391066", weight=0.3)],
        equiv_groups=EQUIV_GROUPS,
    )
    assert any(converging_count(s) >= min_converging for s in distinct)


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
