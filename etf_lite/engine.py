"""Delta computation engine — Python orchestration over the SQL in
:mod:`etf_lite.queries` (ported from the full tracker).

Flow: fetch every per-constituent delta for a source (one query), split it into
additions / removals / changes, aggregate cross-ETF signals by ISIN, then
optionally filter changes and signals through ``config/thresholds.yaml``.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from pathlib import Path

import duckdb
import yaml

from .queries import fetch_daily_deltas
from .cross_etf import aggregate_cross_etf, collapse_moves, converging_count

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_THRESHOLDS_PATH = REPO_ROOT / "config" / "thresholds.yaml"

# A 'change' row that moves neither shares nor weight is noise (e.g. only a
# market-value tick from re-pricing).
_WEIGHT_EPSILON = 0.001


@dataclass
class DeltaResult:
    """Everything the delivery layers need for one source's daily run."""

    as_of_date: date | None
    previous_date: date | None
    source: str
    additions: list[dict] = field(default_factory=list)
    removals: list[dict] = field(default_factory=list)
    changes: list[dict] = field(default_factory=list)
    cross_etf_signals: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    thresholds_applied: bool = False

    def to_dict(self) -> dict:
        """JSON-ready dict (dates as ISO strings)."""
        d = asdict(self)
        d["as_of_date"] = self.as_of_date.isoformat() if self.as_of_date else None
        d["previous_date"] = self.previous_date.isoformat() if self.previous_date else None
        return d


def load_thresholds(path: str | Path | None = None) -> dict:
    """Load ``thresholds.yaml`` (or return sensible defaults if missing)."""
    p = Path(path) if path is not None else DEFAULT_THRESHOLDS_PATH
    if not p.exists():
        logger.warning("thresholds config not found at %s; using built-in defaults", p)
        return {
            "defaults": {
                "min_weight_delta_bps": 25,
                "min_share_pct_change": 5.0,
                "min_value_delta_usd": 1_000_000,
            },
            "overrides_by_vertical": {},
            "cross_etf": {"min_converging_etfs": 2},
        }
    with p.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class DeltaEngine:
    def __init__(self, conn: duckdb.DuckDBPyConnection, config: dict | None = None):
        self.conn = conn
        self.config = config if config is not None else load_thresholds()

    # -- computation -------------------------------------------------------

    def compute_deltas(self, source: str = "web_csv", target_date: date | None = None) -> DeltaResult:
        """Compute all deltas for ``source`` at the latest available date."""
        rows = fetch_daily_deltas(self.conn, source, as_of=target_date)

        additions, removals, changes = [], [], []
        for r in rows:
            dt = r["delta_type"]
            if dt == "addition":
                additions.append(r)
            elif dt == "removal":
                removals.append(r)
            elif self._is_material_change(r):
                changes.append(r)

        equiv_groups = (self.config.get("cross_etf") or {}).get("equivalent_etf_groups")
        signals = aggregate_cross_etf(rows, equiv_groups=equiv_groups)

        as_of, prev = self._resolve_dates(rows)
        etfs = {r["etf_ticker"] for r in rows if r.get("etf_ticker")}
        result = DeltaResult(
            as_of_date=as_of,
            previous_date=prev,
            source=source,
            additions=additions,
            removals=removals,
            changes=changes,
            cross_etf_signals=signals,
            summary={
                "total_additions": len(additions),
                "total_removals": len(removals),
                "total_significant_changes": len(changes),
                "etfs_processed": len(etfs),
            },
        )
        logger.info(
            "Computed deltas [%s] %s vs %s: %d additions, %d removals, %d changes, %d ETFs",
            source, as_of, prev, len(additions), len(removals), len(changes), len(etfs),
        )
        return result

    @staticmethod
    def _is_material_change(r: dict) -> bool:
        delta_shares = r.get("delta_shares") or 0.0
        delta_weight = r.get("delta_weight_pct") or 0.0
        return delta_shares != 0 or abs(delta_weight) > _WEIGHT_EPSILON

    @staticmethod
    def _resolve_dates(rows: list[dict]) -> tuple[date | None, date | None]:
        curr = {r["curr_as_of_date"] for r in rows if r.get("curr_as_of_date")}
        prev = {r["prev_as_of_date"] for r in rows if r.get("prev_as_of_date")}
        return (max(curr) if curr else None, max(prev) if prev else None)

    # -- threshold filtering ----------------------------------------------

    def apply_thresholds(self, raw: DeltaResult, config: dict | None = None) -> DeltaResult:
        config = config if config is not None else self.config
        defaults = config.get("defaults", {})
        overrides = config.get("overrides_by_vertical", {})
        min_converging = config.get("cross_etf", {}).get("min_converging_etfs", 2)

        kept_changes = [c for c in raw.changes if self._passes(c, defaults, overrides)]
        kept_signals = [s for s in raw.cross_etf_signals if converging_count(s) >= min_converging]

        summary = dict(raw.summary)
        summary["total_significant_changes"] = len(kept_changes)
        summary["total_cross_etf_signals"] = len(kept_signals)

        return DeltaResult(
            as_of_date=raw.as_of_date,
            previous_date=raw.previous_date,
            source=raw.source,
            additions=raw.additions,
            removals=raw.removals,
            changes=kept_changes,
            cross_etf_signals=kept_signals,
            summary=summary,
            thresholds_applied=True,
        )

    @staticmethod
    def _passes(change: dict, defaults: dict, overrides: dict) -> bool:
        vertical = change.get("commodity_vertical")
        bands = {**defaults, **overrides.get(vertical, {})}

        delta_weight_bps = abs(change.get("delta_weight_pct") or 0.0) * 100  # % -> bps
        pct_change_shares = abs(change.get("pct_change_shares") or 0.0)
        delta_value = abs(change.get("delta_market_value") or 0.0)

        return (
            delta_weight_bps >= bands.get("min_weight_delta_bps", float("inf"))
            or pct_change_shares >= bands.get("min_share_pct_change", float("inf"))
            or delta_value >= bands.get("min_value_delta_usd", float("inf"))
        )

    # -- convenience -------------------------------------------------------

    def run(self, source: str = "web_csv", target_date: date | None = None,
            apply_thresholds: bool = True) -> DeltaResult:
        raw = self.compute_deltas(source, target_date)
        result = self.apply_thresholds(raw) if apply_thresholds else raw
        equiv = (self.config.get("cross_etf") or {}).get("equivalent_etf_groups")
        if equiv:
            result.additions = collapse_moves(result.additions, equiv)
            result.removals = collapse_moves(result.removals, equiv)
            result.changes = collapse_moves(result.changes, equiv)
        return result


def scope_result(result: DeltaResult, *, keep=None, drop=None) -> DeltaResult:
    """Return a copy of ``result`` restricted to a subset of ETFs.

    Pass ``keep`` (a set of tickers to retain) or ``drop`` (a set to exclude).
    Cross-ETF signals are re-filtered to the surviving funds and dropped if fewer
    than two ETFs remain (the converging threshold). Used to split one delta run
    into the auto-scraped (17) message and the SETM/URNM message.
    """
    keep = set(keep) if keep is not None else None
    drop = set(drop) if drop is not None else set()

    def want(t):
        return (t in keep) if keep is not None else (t not in drop)

    A = [r for r in result.additions if want(r.get("etf_ticker"))]
    R = [r for r in result.removals if want(r.get("etf_ticker"))]
    C = [r for r in result.changes if want(r.get("etf_ticker"))]
    sigs = []
    for s in result.cross_etf_signals:
        det = [d for d in s.get("etf_details", []) if want(d.get("etf_ticker"))]
        n = len({d.get("etf_ticker") for d in det})
        if n >= 2:
            sigs.append({**s, "etf_details": det, "n_etfs": n})

    return replace(
        result, additions=A, removals=R, changes=C, cross_etf_signals=sigs,
        summary={
            **result.summary,
            "total_additions": len(A), "total_removals": len(R),
            "total_significant_changes": len(C), "total_cross_etf_signals": len(sigs),
            "etfs_processed": len({r.get("etf_ticker") for r in (A + R + C)}),
        },
    )
