"""Sprott holdings via a real browser (desktop only).

Sprott fund pages (SETM, URNM) are Cloudflare-gated, so CI can't reach them, but
the full holdings table is rendered into the page HTML (no separate API), and the
"Download All Holdings" control exposes it as a ``data:`` URI:

    Security, Market Value, Symbol, SEDOL, Quantity, Weight

Two halves: pure parsing (unit-testable, no browser) and a Playwright scrape that
drives a persistent, headed Chrome — the persistent profile keeps the Cloudflare
cookie warm, and when a challenge appears the window waits (up to a budget) for a
human to click verify. Playwright is imported lazily.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import time
from datetime import datetime
from urllib.parse import unquote

logger = logging.getLogger(__name__)

FUNDS: dict[str, str] = {
    "SETM": "https://sprottetfs.com/setm-sprott-critical-materials-etf/",
    "URNM": "https://sprottetfs.com/urnm-sprott-uranium-miners-etf/",
}

_DATA_URI_RE = re.compile(r"data:application/csv[^,]*,(.+)", re.IGNORECASE | re.DOTALL)
_HOLDINGS_DATE_RE = re.compile(r"Holdings\s+As of\s+(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE)
_ANY_AS_OF_RE = re.compile(r"As of\s+(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE)


def decode_data_uri(uri: str) -> str:
    m = _DATA_URI_RE.search(uri or "")
    if not m:
        raise ValueError("not a data:application/csv URI")
    return unquote(m.group(1))


def _coerce_date(value: str | None):
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def rows_from_records(records: list[dict], as_of) -> list[dict]:
    """Map Sprott CSV/table records to raw canonical rows (pre-normalisation)."""
    if isinstance(as_of, str):
        as_of = _coerce_date(as_of)

    def pick(r: dict, *names):
        low = {(k or "").strip().lower(): v for k, v in r.items()}
        for n in names:
            if n.lower() in low:
                return low[n.lower()]
        return None

    rows = []
    for r in records:
        name = pick(r, "Security", "Name")
        if not name and not pick(r, "Symbol", "Ticker"):
            continue
        rows.append({
            "constituent_name": name,
            "constituent_ticker": pick(r, "Symbol", "Ticker"),
            "isin": None,
            "sedol": pick(r, "SEDOL"),
            "shares_held": pick(r, "Quantity", "Shares"),
            "market_value": pick(r, "Market Value", "MarketValue"),
            "weight_pct": pick(r, "Weight", "Weight (%)"),
            "currency": "USD",
            "sector": None,
            "country": None,
            "asset_class": None,
            "as_of_date": as_of,
        })
    return rows


def parse_holdings_csv(csv_text: str, as_of) -> list[dict]:
    reader = csv.DictReader(io.StringIO(csv_text))
    records = [r for r in reader if any((v or "").strip() for v in r.values())]
    return rows_from_records(records, as_of)


# --------------------------------------------------------------------------- #
# Browser scrape (desktop only; Playwright imported lazily)
# --------------------------------------------------------------------------- #
def _extract_as_of(page_text: str):
    m = _HOLDINGS_DATE_RE.search(page_text) or _ANY_AS_OF_RE.search(page_text)
    return _coerce_date(m.group(1)) if m else None


def scrape_fund(page, etf_ticker: str, url: str, *, wait_budget_s: int) -> list[dict]:
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)

    deadline = time.monotonic() + wait_budget_s
    href = None
    while time.monotonic() < deadline:
        try:
            link = page.locator("a:has-text('Download All Holdings')").first
            if link.count():
                href = link.get_attribute("href", timeout=2_000)
                if href and href.startswith("data:"):
                    break
        except Exception:  # noqa: BLE001 - element not ready yet
            pass
        logger.info("[%s] waiting for holdings / Cloudflare verify…", etf_ticker)
        page.wait_for_timeout(15_000)

    page_text = page.inner_text("body")
    as_of = _extract_as_of(page_text)

    if href and href.startswith("data:"):
        rows = parse_holdings_csv(decode_data_uri(href), as_of)
        if rows:
            return rows

    rows = _scrape_table(page, as_of)
    if not rows:
        raise RuntimeError(f"[{etf_ticker}] no holdings found within {wait_budget_s}s "
                           f"(Cloudflare not cleared, or layout changed)")
    return rows


def _scrape_table(page, as_of) -> list[dict]:
    js = """
    () => {
      const wanted = ['security','market value','symbol','sedol','quantity','weight'];
      for (const t of document.querySelectorAll('table')) {
        const heads = [...t.querySelectorAll('th,thead td')].map(e => e.innerText.trim().toLowerCase());
        if (!wanted.every(w => heads.some(h => h.includes(w)))) continue;
        const out = [];
        for (const tr of t.querySelectorAll('tbody tr')) {
          const c = [...tr.querySelectorAll('td')].map(e => e.innerText.trim());
          if (c.length >= 6) out.push(c);
        }
        return out;
      }
      return [];
    }
    """
    cells = page.evaluate(js)
    records = [
        {"Security": c[0], "Market Value": c[1], "Symbol": c[2],
         "SEDOL": c[3], "Quantity": c[4], "Weight": c[5]}
        for c in cells
    ]
    return rows_from_records(records, as_of)


def scrape_all(funds: dict[str, str] | None = None, *, wait_budget_s: int = 14400,
               profile_dir: str | None = None, headless: bool = False) -> dict[str, list[dict]]:
    """Scrape every fund in one browser session. ``wait_budget_s`` is the OVERALL
    patience (default 4h). Returns ``{ticker: raw_rows}``."""
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    funds = funds or FUNDS
    profile = profile_dir or str(Path.home() / ".sprott_scraper_profile")

    out: dict[str, list[dict]] = {}
    start = time.monotonic()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile, headless=headless, args=["--start-maximized"], no_viewport=True,
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for ticker, url in funds.items():
                remaining = max(60, int(wait_budget_s - (time.monotonic() - start)))
                try:
                    out[ticker] = scrape_fund(page, ticker, url, wait_budget_s=remaining)
                    logger.info("[%s] scraped %d rows", ticker, len(out[ticker]))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[%s] scrape failed: %s", ticker, exc)
                    out[ticker] = []
        finally:
            ctx.close()
    return out
