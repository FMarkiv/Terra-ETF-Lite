"""Persistent store — committed CSV snapshots, rebuilt into in-memory DuckDB.

GitHub Actions runners are ephemeral, so the day-over-day history that the delta
engine needs lives in the repo as one append-only CSV per ETF
(``data/snapshots/{TICKER}.csv``). Each daily run appends today's holdings (if
the as-of date is new) and the workflow commits the change back.

At delta time we load every snapshot CSV into an in-memory DuckDB table
(``etf_holdings_snapshot``) plus an ``etf_universe`` table built from the seed —
the same two relations the ported delta SQL expects.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from .universe import UNIVERSE

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots"

# CSV column order (canonical schema + keys). snapshot_id/ingested_at are added
# at load time, not stored.
COLUMNS = [
    "as_of_date", "etf_ticker", "constituent_ticker", "constituent_name", "isin",
    "sedol", "shares_held", "market_value", "weight_pct", "currency", "sector",
    "country", "asset_class", "instrument_type", "source",
]

SOURCE = "web_csv"


def _path(etf_ticker: str) -> Path:
    return SNAPSHOT_DIR / f"{etf_ticker}.csv"


def existing_dates(etf_ticker: str) -> set[str]:
    """ISO date strings already stored for an ETF."""
    p = _path(etf_ticker)
    if not p.exists():
        return set()
    try:
        df = pd.read_csv(p, usecols=["as_of_date"], dtype=str)
    except Exception:  # noqa: BLE001 - empty/corrupt file
        return set()
    return set(df["as_of_date"].dropna().astype(str))


def primary_date(rows: list[dict]) -> date | None:
    """The snapshot's as-of date: the latest non-null date across its rows."""
    dates = sorted({r["as_of_date"] for r in rows if r.get("as_of_date")})
    return dates[-1] if dates else None


def append_snapshot(etf_ticker: str, rows: list[dict]) -> tuple[str, str | None, int]:
    """Append a day's normalised rows for one ETF (dedup by as-of date).

    Returns ``(status, as_of_iso, n_rows)`` where status is
    ``ingested`` | ``skipped`` | ``no_data``.
    """
    as_of = primary_date(rows)
    if as_of is None or not rows:
        return ("no_data", None, 0)
    as_of_iso = as_of.isoformat()

    if as_of_iso in existing_dates(etf_ticker):
        return ("skipped", as_of_iso, 0)

    out = []
    for r in rows:
        rec = {c: r.get(c) for c in COLUMNS}
        rec["as_of_date"] = as_of_iso          # one date per file
        rec["etf_ticker"] = etf_ticker
        rec["source"] = SOURCE
        out.append(rec)

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(out, columns=COLUMNS)
    p = _path(etf_ticker)
    df.to_csv(p, mode="a", header=not p.exists(), index=False)
    return ("ingested", as_of_iso, len(out))


def _apply_isin_overrides(conn: duckdb.DuckDBPyConnection) -> None:
    """Back-fill NULL ISINs in the loaded history from config/isin_overrides.yaml.

    The delta key is COALESCE(isin, ticker); if overrides reached only new rows,
    a name would flip key mid-history and show as a phantom removal + addition.
    Applied in memory only — the committed snapshot CSVs stay as ingested.
    """
    from .isin_resolver import IsinResolver, load_overrides

    ov = load_overrides()
    if not (ov.get("by_ticker") or ov.get("by_name")):
        return
    resolver = IsinResolver([], ov)
    rows = conn.execute(
        "SELECT DISTINCT constituent_ticker, constituent_name, country "
        "FROM etf_holdings_snapshot WHERE isin IS NULL"
    ).fetchall()
    fills = [(t, n, c, resolver.resolve(t, n, c)[0]) for t, n, c in rows]
    fills = [f for f in fills if f[3]]
    if not fills:
        return
    conn.register("isin_fill_df", pd.DataFrame(fills, columns=["t", "n", "c", "isin"]))
    conn.execute(
        "UPDATE etf_holdings_snapshot h SET isin = f.isin FROM isin_fill_df f "
        "WHERE h.isin IS NULL "
        "AND h.constituent_ticker IS NOT DISTINCT FROM f.t "
        "AND h.constituent_name IS NOT DISTINCT FROM f.n "
        "AND h.country IS NOT DISTINCT FROM f.c"
    )
    conn.unregister("isin_fill_df")


def load_connection() -> duckdb.DuckDBPyConnection:
    """Build an in-memory DuckDB with ``etf_holdings_snapshot`` + ``etf_universe``."""
    conn = duckdb.connect(":memory:")

    # Universe (for the commodity_vertical join in the delta SQL).
    uni = pd.DataFrame(UNIVERSE)
    conn.register("uni_df", uni)
    conn.execute("CREATE TABLE etf_universe AS SELECT * FROM uni_df")

    files = sorted(SNAPSHOT_DIR.glob("*.csv")) if SNAPSHOT_DIR.exists() else []
    if files:
        glob = str(SNAPSHOT_DIR / "*.csv")
        conn.execute(
            "CREATE TABLE etf_holdings_snapshot AS "
            "SELECT ROW_NUMBER() OVER () AS snapshot_id, * "
            "FROM read_csv_auto(?, header=true, union_by_name=true)",
            [glob],
        )
        # If every snapshot predates the classification columns, union_by_name
        # won't create them — add them (NULL) so the delta SQL can reference them.
        existing = {r[1] for r in conn.execute(
            "PRAGMA table_info('etf_holdings_snapshot')").fetchall()}
        for col in ("asset_class", "instrument_type"):
            if col not in existing:
                conn.execute(f"ALTER TABLE etf_holdings_snapshot ADD COLUMN {col} VARCHAR")
        _apply_isin_overrides(conn)
    else:
        # First run, no history yet — create an empty, correctly-typed table.
        conn.execute(
            """
            CREATE TABLE etf_holdings_snapshot (
                snapshot_id BIGINT, as_of_date DATE, etf_ticker VARCHAR,
                constituent_ticker VARCHAR, constituent_name VARCHAR, isin VARCHAR,
                sedol VARCHAR, shares_held DOUBLE, market_value DOUBLE,
                weight_pct DOUBLE, currency VARCHAR, sector VARCHAR,
                country VARCHAR, asset_class VARCHAR, instrument_type VARCHAR,
                source VARCHAR
            )
            """
        )
    return conn
