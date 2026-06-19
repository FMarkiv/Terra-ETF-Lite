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


def aggregate_cross_etf(deltas: list[dict]) -> list[dict]:
    """Group ``deltas`` by ISIN into per-constituent flow signals."""
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
        if etf not in g["_etfs_seen"]:
            g["_etfs_seen"].add(etf)
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
