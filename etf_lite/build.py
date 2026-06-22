"""Daily build: scrape the 17 ETFs -> append snapshots -> compute deltas ->
render the static dashboard into ``site/``.

Run locally or in CI:

    python -m etf_lite.build

Exit code 0 if at least one ETF ingested or was already current; 1 if every
ETF failed (so CI surfaces a total outage).
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

from .engine import DeltaEngine
from .isin_resolver import IsinResolver
from .normaliser import normalise_holdings
from .parsers import get_parser
from .parsers._common import make_session
from .store import append_snapshot, load_connection, primary_date
from .universe import UNIVERSE

logger = logging.getLogger("etf_lite.build")

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = REPO_ROOT / "frontend"
SITE_DIR = REPO_ROOT / "site"


def _fetch_all(session) -> tuple[list[dict], list[dict]]:
    """Fetch + normalise every ETF. Returns (per_etf, all_rows).

    ``per_etf`` carries one record per ETF: ticker, vertical, status, rows,
    error. ``all_rows`` is the flat list of ISIN-bearing rows used to build the
    cross-fund ISIN crosswalk.
    """
    per_etf = []
    for spec in UNIVERSE:
        ticker = spec["etf_ticker"]
        rec = {"etf_ticker": ticker, "commodity_vertical": spec["commodity_vertical"],
               "status": "failed", "rows": [], "error": None}
        # External funds (e.g. Sprott) are fed by a desktop scraper that commits
        # their CSVs here; CI must not try to fetch them (no parser, Cloudflare).
        if spec.get("external"):
            rec["status"], rec["error"] = "external", None
            per_etf.append(rec)
            continue
        try:
            parser = get_parser(spec["issuer"])
            raw = parser.fetch_holdings(ticker, spec["fund_page_url"], session=session)
            rows = normalise_holdings(raw, ticker)
            if not rows:
                rec["status"], rec["error"] = "no_data", "no usable holdings"
            else:
                rec["status"], rec["rows"] = "fetched", rows
        except Exception as exc:  # noqa: BLE001 - isolate per-ETF failures
            rec["error"] = str(exc)
            logger.warning("[%s] FAILED: %s", ticker, exc)
        per_etf.append(rec)
    all_rows = [r for e in per_etf for r in e["rows"]]
    return per_etf, all_rows


def ingest() -> list[dict]:
    """Scrape all ETFs, resolve ISINs cross-fund, append new snapshots.

    Returns the per-ETF status list (status now one of
    ``ingested`` | ``skipped`` | ``no_data`` | ``failed``).
    """
    session = make_session()
    per_etf, all_rows = _fetch_all(session)

    # Build the ISIN crosswalk from THIS run's combined holdings + overrides, then
    # fill any still-missing ISINs (e.g. Global X names resolved via iShares PICK).
    resolver = IsinResolver.from_rows(all_rows)
    for e in per_etf:
        for r in e["rows"]:
            if r.get("isin") is None and (r.get("constituent_ticker") or r.get("constituent_name")):
                isin, _ = resolver.resolve(r.get("constituent_ticker"),
                                           r.get("constituent_name"), r.get("country"))
                if isin:
                    r["isin"] = isin

    # Append new snapshots (dedup by as-of date). Reject future-dated feeds: a
    # holdings file can't be "as of" a date after we fetched it (e.g. Amplify's
    # feed has reported a forward date) — storing it would hijack the headline
    # date and manufacture phantom deltas.
    run_date = date.today()
    for e in per_etf:
        if e["status"] == "fetched":
            pdate = primary_date(e["rows"])
            if pdate and pdate > run_date:
                e["status"] = "future_date"
                e["error"] = f"feed dated {pdate} > run date {run_date} — not ingested"
                e["as_of_date"], e["n_today"] = None, 0
                logger.warning("[%s] feed reports future as-of %s (today %s) — skipping",
                               e["etf_ticker"], pdate, run_date)
            else:
                status, as_of, n = append_snapshot(e["etf_ticker"], e["rows"])
                e["status"], e["as_of_date"], e["n_today"] = status, as_of, n
        else:
            e["as_of_date"], e["n_today"] = None, 0
        e.pop("rows", None)  # drop bulky rows before returning/serialising
        logger.info("[%s] %s (as_of=%s, n=%s)", e["etf_ticker"], e["status"],
                    e.get("as_of_date"), e.get("n_today"))
    return per_etf


def build_site(etf_status: list[dict]) -> dict:
    """Compute deltas from the full history and render the static dashboard."""
    conn = load_connection()
    try:
        result = DeltaEngine(conn).run(source="web_csv", apply_thresholds=True)
        # Per-ETF latest stored date (freshness panel).
        freshness = {
            row[0]: row[1].isoformat() if row[1] else None
            for row in conn.execute(
                "SELECT etf_ticker, MAX(as_of_date) FROM etf_holdings_snapshot "
                "WHERE source='web_csv' GROUP BY etf_ticker"
            ).fetchall()
        }
    finally:
        conn.close()

    for e in etf_status:
        e["latest_stored"] = freshness.get(e["etf_ticker"])

    payload = result.to_dict()
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload["etfs"] = sorted(etf_status, key=lambda x: x["etf_ticker"])
    payload["coverage"] = {
        "tracked": len(UNIVERSE),
        "ingested": sum(1 for e in etf_status if e["status"] == "ingested"),
        "skipped": sum(1 for e in etf_status if e["status"] == "skipped"),
        "failed": sum(1 for e in etf_status if e["status"] in ("failed", "no_data", "future_date")),
        "external": sum(1 for e in etf_status if e["status"] == "external"),
    }

    # Render: copy the static frontend + write the data payload alongside it.
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "styles.css", "app.js"):
        shutil.copyfile(FRONTEND_DIR / name, SITE_DIR / name)
    (SITE_DIR / "data.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    logger.info("Wrote %s", SITE_DIR / "data.json")
    return payload


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("=== etf-flow-lite build START ===")
    etf_status = ingest()
    payload = build_site(etf_status)

    cov = payload["coverage"]
    logger.info("=== build END — %d ingested, %d skipped, %d failed of %d tracked ===",
                cov["ingested"], cov["skipped"], cov["failed"], cov["tracked"])
    # Total outage (every ETF failed) -> non-zero exit.
    return 1 if cov["ingested"] == 0 and cov["skipped"] == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
