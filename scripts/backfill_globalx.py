"""Backfill Global X US snapshots from the dated assets.globalxetfs.com files.

Global X's fund pages stopped exposing the CSV link (Oct 2026), leaving a gap in
COPX/LIT/SIL/URA history. The CDN still serves every day's
``<slug>_full-holdings_YYYYMMDD.csv``, so this walks a date range and appends
any missing as-of dates through the normal parse -> normalise -> ISIN -> store
path (dedup by as-of date, so re-running is safe).

    python -m scripts.backfill_globalx 2026-10-13 2026-11-18 [COPX LIT ...]
"""

from __future__ import annotations

import logging
import sys
from datetime import date, timedelta

import pandas as pd

from etf_lite.isin_resolver import IsinResolver
from etf_lite.normaliser import normalise_holdings
from etf_lite.parsers._common import make_session
from etf_lite.parsers.globalx import _ASSET_URL, GlobalXParser
from etf_lite.store import SNAPSHOT_DIR, append_snapshot, existing_dates

DEFAULT_TICKERS = ["COPX", "LIT", "SIL", "URA"]
logger = logging.getLogger("backfill_globalx")


def _resolver() -> IsinResolver:
    """Crosswalk from all stored snapshots (same role as a run's combined rows)."""
    frames = [pd.read_csv(p, dtype=str) for p in SNAPSHOT_DIR.glob("*.csv")]
    df = pd.concat(frames, ignore_index=True).astype(object)
    rows = df.where(pd.notna(df), None).to_dict("records")
    return IsinResolver.from_rows(rows)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    start, end = date.fromisoformat(argv[0]), date.fromisoformat(argv[1])
    tickers = [t.upper() for t in argv[2:]] or DEFAULT_TICKERS
    session, parser, resolver = make_session(), GlobalXParser(), _resolver()
    added = 0
    for ticker in tickers:
        have = existing_dates(ticker)
        d = start
        while d <= end:
            if d.weekday() < 5 and d.isoformat() not in have:
                url = _ASSET_URL.format(slug=ticker.lower(), ymd=d.strftime("%Y%m%d"))
                try:
                    raw = parser.fetch_holdings(ticker, csv_url=url, session=session)
                except Exception as exc:  # noqa: BLE001 - 404 on holidays etc.
                    logger.info("[%s] %s: no file (%s)", ticker, d, str(exc)[:80])
                else:
                    rows = normalise_holdings(raw, ticker)
                    for r in rows:
                        if r.get("isin") is None and (r.get("constituent_ticker") or r.get("constituent_name")):
                            isin, _ = resolver.resolve(r.get("constituent_ticker"),
                                                       r.get("constituent_name"), r.get("country"))
                            if isin:
                                r["isin"] = isin
                    status, as_of, n = append_snapshot(ticker, rows)
                    logger.info("[%s] %s -> %s as_of=%s n=%d", ticker, d, status, as_of, n)
                    added += status == "ingested"
            d += timedelta(days=1)
    logger.info("backfill done: %d snapshots added", added)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
