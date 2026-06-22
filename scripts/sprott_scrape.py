"""Desktop Sprott scraper (lite) — the SETM/URNM bridge.

Runs on the desktop (Sprott is Cloudflare-gated, so it needs a real browser; if a
challenge appears the window waits up to 4h for a human to click verify). Per run:

  1. scrape SETM + URNM (browser),
  2. resolve ISINs from the 17 auto-scraped funds' committed crosswalk,
  3. write the lite snapshot CSVs,
  4. commit + push + trigger the Pages rebuild,
  5. send ONE combined SETM+URNM Telegram message.

The 17-fund message is sent separately by CI (scripts/notify.py). Seed/test from
a saved download instead of the browser:

    python scripts/sprott_scrape.py --from-file ../Sprott.md --as-of 2026-06-18 --no-push --no-telegram
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_lite import sprott as sb  # noqa: E402
from etf_lite.engine import DeltaEngine, scope_result  # noqa: E402
from etf_lite.formatter import format_alert  # noqa: E402
from etf_lite.isin_resolver import IsinResolver, load_overrides  # noqa: E402
from etf_lite.normaliser import normalise_holdings  # noqa: E402
from etf_lite.store import append_snapshot, load_connection  # noqa: E402
from etf_lite.telegram import TelegramConfigError, TelegramSender  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("sprott")

REPO_ROOT = Path(__file__).resolve().parents[1]
SPROTT_TICKERS = {"SETM", "URNM"}
TITLE = "🟣 Sprott Flow — SETM/URNM"


def rows_from_file(path: Path, as_of) -> dict[str, list[dict]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    uris = re.findall(r"data:application/csv[^\s]+", text)
    if len(uris) < 2:
        raise SystemExit(f"Expected 2 data: URIs in {path}, found {len(uris)}")
    return {
        "SETM": sb.parse_holdings_csv(sb.decode_data_uri(uris[0]), as_of),
        "URNM": sb.parse_holdings_csv(sb.decode_data_uri(uris[1]), as_of),
    }


def resolve_isins(raw_by_fund: dict[str, list[dict]]) -> None:
    """Fill missing ISINs from the committed crosswalk (the 17 funds carry ISINs
    that overlap the Sprott holdings — Cameco, Freeport, NexGen, …)."""
    conn = load_connection()
    try:
        rows = conn.execute(
            "SELECT constituent_ticker, constituent_name, country, isin "
            "FROM etf_holdings_snapshot WHERE isin IS NOT NULL AND isin <> 'CASH'"
        ).fetchall()
    finally:
        conn.close()
    resolver = IsinResolver(rows, load_overrides())

    n = 0
    for fund_rows in raw_by_fund.values():
        for r in fund_rows:
            if not r.get("isin") and (r.get("constituent_ticker") or r.get("constituent_name")):
                isin, _ = resolver.resolve(r.get("constituent_ticker"),
                                           r.get("constituent_name"), r.get("country"))
                if isin:
                    r["isin"] = isin
                    n += 1
    logger.info("Resolved %d ISIN(s) from the crosswalk", n)


def publish() -> None:
    def git(*args):
        return subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True)

    git("add", "data/snapshots")
    if git("diff", "--cached", "--quiet").returncode == 0:
        logger.info("No new Sprott snapshots to publish.")
        return
    git("commit", "-m", f"data: Sprott (SETM/URNM) snapshots {date.today().isoformat()}")
    if git("push", "origin", "main").returncode != 0:
        logger.warning("Push failed — resolve and re-run.")
        return
    gh = subprocess.run(["gh", "workflow", "run", "daily.yml", "-R", "FMarkiv/Terra-ETF-Lite"],
                        capture_output=True, text=True)
    logger.info("Pushed + triggered Pages rebuild." if gh.returncode == 0
                else f"Pushed; Pages trigger failed: {gh.stderr.strip()}")


def notify(config_path: str) -> None:
    conn = load_connection()
    try:
        full = DeltaEngine(conn).run(source="web_csv", apply_thresholds=True)
    finally:
        conn.close()
    scoped = scope_result(full, keep=SPROTT_TICKERS)

    if not (scoped.additions or scoped.removals or scoped.changes):
        logger.info("Sprott: no material deltas — no Telegram message.")
        return
    overrides = {"title": TITLE}
    try:
        TelegramSender(config_path=config_path, overrides=overrides).send_delta_alert_sync(scoped)
        logger.info("Sprott Telegram message sent (SETM + URNM).")
    except TelegramConfigError as exc:
        logger.warning("Telegram not configured (%s). Preview:\n%s", exc, format_alert(scoped, overrides))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Desktop Sprott (SETM/URNM) scraper")
    ap.add_argument("--from-file", help="parse Sprott data: URIs from a file instead of the browser")
    ap.add_argument("--as-of", help="as-of date (YYYY-MM-DD) for --from-file")
    ap.add_argument("--wait", type=int, default=14400, help="browser wait budget seconds (default 14400 = 4h)")
    ap.add_argument("--headless", action="store_true", help="headless browser (no human verify possible)")
    ap.add_argument("--no-push", action="store_true", help="don't commit/push/trigger the site")
    ap.add_argument("--no-telegram", action="store_true", help="don't send the Telegram message")
    ap.add_argument("--config", default="config/telegram.yaml", help="telegram.yaml path (desktop)")
    args = ap.parse_args(argv)

    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    if args.from_file:
        raw_by_fund = rows_from_file(Path(args.from_file), as_of)
    else:
        raw_by_fund = sb.scrape_all(wait_budget_s=args.wait, headless=args.headless)
    raw_by_fund = {k: v for k, v in raw_by_fund.items() if v}
    if not raw_by_fund:
        logger.error("No Sprott holdings acquired — nothing to do.")
        return 1

    resolve_isins(raw_by_fund)

    run_date = date.today()
    for ticker, rows in raw_by_fund.items():
        norm = normalise_holdings(rows, ticker)
        dates = sorted({r["as_of_date"] for r in norm if r["as_of_date"]})
        if dates and dates[-1] > run_date:
            logger.warning("[%s] future as-of %s — skipping", ticker, dates[-1])
            continue
        status, asof, n = append_snapshot(ticker, norm)
        logger.info("[%s] %s (as_of=%s, n=%s)", ticker, status, asof, n)

    if not args.no_push:
        publish()
    if not args.no_telegram:
        notify(args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
