"""Render the static dashboard from the committed snapshots — no scrape, no
Telegram. Used by the on:push Pages workflow after the desktop Sprott scraper
commits SETM/URNM, so live Pages reflects them without re-scraping the 17 funds
or re-sending the daily alert.

    python scripts/render_site.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_lite.build import build_site  # noqa: E402
from etf_lite.store import load_connection  # noqa: E402
from etf_lite.universe import UNIVERSE  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("render_site")


def _stored_tickers() -> set[str]:
    conn = load_connection()
    try:
        return {r[0] for r in conn.execute(
            "SELECT DISTINCT etf_ticker FROM etf_holdings_snapshot").fetchall()}
    finally:
        conn.close()


def main() -> int:
    # No scrape: derive each fund's status from the store. 'skipped' == already
    # stored/current (build_site fills latest_stored from the snapshots), so the
    # coverage panel's "current" count stays accurate without re-fetching.
    have = _stored_tickers()
    etf_status = []
    for spec in UNIVERSE:
        ticker = spec["etf_ticker"]
        if spec.get("external"):
            status = "external"
        elif ticker in have:
            status = "skipped"
        else:
            status = "no_data"
        etf_status.append({
            "etf_ticker": ticker,
            "commodity_vertical": spec["commodity_vertical"],
            "status": status,
            "error": None,
        })

    payload = build_site(etf_status)
    cov = payload["coverage"]
    logger.info("Rendered site from store — %d funds with data, %d external, %d tracked",
                cov["skipped"], cov["external"], cov["tracked"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
