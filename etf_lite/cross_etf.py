"""Cross-ETF flow aggregation (ported verbatim).

The single-ETF deltas tell you *that* an ETF added BHP. The cross-ETF view tells
you that *three* mining ETFs added BHP on the same day — consensus that is
invisible when checking provider sites one at a time. That consensus is the core
value proposition of the tracker.

Groups deltas by ISIN (the stable cross-ETF join key) and counts how many
distinct ETFs moved it in each direction. Mirrors :func:`queries.cross_etf_sql`.
"""

from __future__ import annotations

# Pseudo-ISINs that must never be grouped across ETFs.
_NON_JOINABLE = {None, "", "CASH"}


def _build_group_index(equiv_groups: list[list[str]] | None) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Build lookup structures from the configured equivalence groups.

    Returns ``(ticker_to_group, group_to_order)``: each ticker maps to its
    group key (the first ticker listed in its group); tickers not listed map to
    themselves (every ungrouped fund is its own group of one). ``group_to_order``
    keeps the configured ticker order so the representative can be chosen.
    """
    ticker_to_group: dict[str, str] = {}
    group_to_order: dict[str, list[str]] = {}
    for grp in equiv_groups or []:
        if not grp:
            continue
        key = grp[0]
        group_to_order[key] = list(grp)
        for t in grp:
            ticker_to_group[t] = key
    return ticker_to_group, group_to_order


def _collapse_cross_listings(
    deltas: list[dict], ticker_to_group: dict[str, str], group_to_order: dict[str, list[str]]
) -> list[dict]:
    """Collapse cross-listed ETFs to one representative delta per (ISIN, group).

    Cross-listings of the same fund/index (e.g. GDX, GDX.L, GDX-ASX) track one
    underlying portfolio, so they contribute a single vote — not several — to
    the cross-ETF consensus. For each (ISIN, group) we keep one delta: the
    representative is the first ticker in the group's configured order that is
    present (its delta is kept as-is). Ungrouped funds map to their own
    single-ticker group and pass through unchanged.
    """
    chosen: dict[tuple, dict] = {}

    def rank(group_key: str, ticker: str) -> int:
        order = group_to_order.get(group_key)
        if order and ticker in order:
            return order.index(ticker)
        return 0

    for d in deltas:
        ticker = d.get("etf_ticker")
        group_key = ticker_to_group.get(ticker, ticker)
        k = (d.get("isin"), group_key)
        existing = chosen.get(k)
        if existing is None or rank(group_key, ticker) < rank(group_key, existing.get("etf_ticker")):
            chosen[k] = d
    return list(chosen.values())


def aggregate_cross_etf(deltas: list[dict], equiv_groups: list[list[str]] | None = None) -> list[dict]:
    """Group ``deltas`` by ISIN into per-constituent flow signals.

    ``equiv_groups`` is an optional list of cross-listing equivalence groups
    (lists of tickers tracking the same fund/index, e.g. ``["GDX", "GDX.L",
    "GDX-ASX"]``). ETFs in the same group collapse to a single vote per ISIN so
    a name held across cross-listings counts as ONE converging fund. Absent
    groups means no collapsing (each ticker is its own group).
    """
    ticker_to_group, group_to_order = _build_group_index(equiv_groups)
    if equiv_groups:
        deltas = _collapse_cross_listings(deltas, ticker_to_group, group_to_order)

    groups: dict[str, dict] = {}

    for d in deltas:
        isin = d.get("isin")
        if isin in _NON_JOINABLE:
            continue

        g = groups.get(isin)
        if g is None:
            g = groups[isin] = {
                "isin": isin,
                "constituent_name": d.get("constituent_name"),
                "constituent_ticker": d.get("constituent_ticker"),
                "n_etfs": 0,
                "n_etfs_weight_up": 0,
                "n_etfs_weight_down": 0,
                "n_etfs_added": 0,
                "n_etfs_removed": 0,
                "total_delta_market_value": 0.0,
                "_etfs_seen": set(),
                "etf_details": [],
            }

        etf = d.get("etf_ticker")
        # Count distinct equivalence GROUPS, not raw tickers: cross-listings of
        # the same fund/index collapse to one vote. Ungrouped funds map to
        # themselves, so this is distinct-ETF counting when no groups are set.
        group_key = ticker_to_group.get(etf, etf)
        if group_key not in g["_etfs_seen"]:
            g["_etfs_seen"].add(group_key)
            g["n_etfs"] += 1

        delta_type = d.get("delta_type")
        delta_weight = d.get("delta_weight_pct") or 0.0
        if delta_type == "addition":
            g["n_etfs_added"] += 1
        elif delta_type == "removal":
            g["n_etfs_removed"] += 1
        if delta_weight > 0:
            g["n_etfs_weight_up"] += 1
        elif delta_weight < 0:
            g["n_etfs_weight_down"] += 1

        g["total_delta_market_value"] += d.get("delta_market_value") or 0.0
        if not g["constituent_name"] and d.get("constituent_name"):
            g["constituent_name"] = d["constituent_name"]
        if not g["constituent_ticker"] and d.get("constituent_ticker"):
            g["constituent_ticker"] = d["constituent_ticker"]
        g["etf_details"].append(
            {
                "etf_ticker": d.get("etf_ticker"),
                "delta_type": delta_type,
                "delta_weight_pct": d.get("delta_weight_pct"),
                "delta_shares": d.get("delta_shares"),
                "delta_market_value": d.get("delta_market_value"),
            }
        )

    signals = list(groups.values())
    for g in signals:
        del g["_etfs_seen"]
    signals.sort(key=lambda g: abs(g["total_delta_market_value"] or 0.0), reverse=True)
    return signals


def converging_count(signal: dict) -> int:
    """Number of *distinct* ETFs that touched this constituent — the consensus
    magnitude (not the sum of the four direction counters, which double-counts a
    one-ETF move)."""
    return signal["n_etfs"]
