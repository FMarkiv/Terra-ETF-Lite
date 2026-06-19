"""One-off migration: seed the lite CSV snapshot store from the full tracker's
DuckDB, so deltas are meaningful from the first run instead of showing every
holding as a brand-new addition.

Exports the ``web_csv`` holdings history for the 17 lite ETFs into
``data/snapshots/{TICKER}.csv`` (the same format the daily build appends to),
overwriting any existing files. Safe to re-run.

Usage:
    python scripts/backfill_from_duckdb.py [path/to/etf_holdings.duckdb]

Default source DB: ../data/etf_holdings.duckdb (the full tracker, one level up).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd

# Make the package importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_lite.store import COLUMNS, SNAPSHOT_DIR  # noqa: E402
from etf_lite.universe import UNIVERSE  # noqa: E402

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "etf_holdings.duckdb"


def main(argv: list[str]) -> int:
    db_path = Path(argv[0]) if argv else DEFAULT_DB
    if not db_path.exists():
        print(f"Source DB not found: {db_path}")
        return 1

    tickers = [u["etf_ticker"] for u in UNIVERSE]
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        # Select exactly the lite CSV columns, web_csv source only.
        select_cols = ", ".join(c for c in COLUMNS if c != "source")
        placeholders = ", ".join("?" for _ in tickers)
        total = 0
        for ticker in tickers:
            df = conn.execute(
                f"SELECT {select_cols} FROM etf_holdings_snapshot "
                f"WHERE source = 'web_csv' AND etf_ticker = ? "
                f"ORDER BY as_of_date, constituent_ticker",
                [ticker],
            ).df()
            if df.empty:
                print(f"  {ticker:<10} no web_csv rows — skipped")
                continue
            df["source"] = "web_csv"
            df = df[COLUMNS]  # enforce column order
            out = SNAPSHOT_DIR / f"{ticker}.csv"
            df.to_csv(out, index=False)
            ndates = df["as_of_date"].nunique()
            total += len(df)
            print(f"  {ticker:<10} {len(df):>5} rows, {ndates} date(s) -> {out.name}")
        print(f"Backfill complete: {total} rows across {len(tickers)} ETFs.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
