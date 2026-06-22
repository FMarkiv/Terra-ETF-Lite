"""Send the auto-scraped (17-fund) Telegram alert.

Run by the GitHub Action after the site build, using the TELEGRAM_BOT_TOKEN /
TELEGRAM_CHAT_ID secrets. Computes web_csv deltas, scopes them to the
auto-scraped funds (excludes the external SETM/URNM — those get their own
message from the desktop scraper), and sends.

    python scripts/notify.py              # send
    python scripts/notify.py --dry-run    # print, don't send
    python scripts/notify.py --notify-on-empty
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_lite.engine import DeltaEngine, scope_result  # noqa: E402
from etf_lite.formatter import format_alert  # noqa: E402
from etf_lite.store import load_connection  # noqa: E402
from etf_lite.telegram import TelegramConfigError, TelegramSender  # noqa: E402
from etf_lite.universe import UNIVERSE  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("notify")

EXTERNAL = {u["etf_ticker"] for u in UNIVERSE if u.get("external")}
TITLE = "📊 Mining ETF Flow"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Send the 17-fund Telegram alert")
    ap.add_argument("--dry-run", action="store_true", help="print the message instead of sending")
    ap.add_argument("--notify-on-empty", action="store_true", help="send even when nothing moved")
    args = ap.parse_args(argv)

    conn = load_connection()
    try:
        result = DeltaEngine(conn).run(source="web_csv", apply_thresholds=True)
    finally:
        conn.close()
    scoped = scope_result(result, drop=EXTERNAL)

    has = bool(scoped.additions or scoped.removals or scoped.changes)
    if not has and not args.notify_on_empty:
        logger.info("No material deltas for the auto-scraped funds — no message.")
        return 0

    overrides = {"title": TITLE}
    if args.dry_run:
        print(format_alert(scoped, overrides))
        return 0
    try:
        ok = TelegramSender(overrides=overrides).send_delta_alert_sync(scoped)
    except TelegramConfigError as exc:
        logger.warning("Telegram not configured (%s). Preview:\n%s", exc, format_alert(scoped, overrides))
        return 0
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
