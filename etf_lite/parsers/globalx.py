"""Global X holdings parser.

ETFs: COPX, SIL, URA, LIT (US, globalxetfs.com) and 4COP (UCITS, globalxetfs.eu).
US gives SEDOL (no ISIN — resolved later); EU UCITS includes ISIN.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import requests

from ..base import HoldingsAdapter
from ._common import (
    DEFAULT_TIMEOUT,
    HoldingsFetchError,
    csv_records,
    extract_as_of_date,
    find_header_index,
    http_get,
    looks_like_html,
    make_session,
    pick,
    resolve_csv_url,
)

logger = logging.getLogger(__name__)

_CSV_LINK_RE = re.compile(
    r"https://assets\.globalxetfs\.com/funds/holdings/[^\s\"']+?_full-holdings_\d{8}\.csv"
)
_EU_SLUG_RE = re.compile(r"globalxetfs\.eu/funds/([^/?#]+)", re.I)
_US_SLUG_RE = re.compile(r"globalxetfs\.com/funds/([^/?#]+)", re.I)
_ASSET_URL = "https://assets.globalxetfs.com/funds/holdings/{slug}_full-holdings_{ymd}.csv"


def _is_eu(url: str | None) -> bool:
    return bool(url) and "globalxetfs.eu" in url.lower()


def _eu_csv_url(fund_page_url: str) -> str | None:
    m = _EU_SLUG_RE.search(fund_page_url or "")
    return f"https://globalxetfs.eu/api/funds/{m.group(1)}/topholdingscsv" if m else None


def _probe_dated_csv(
    fund_page_url: str, *, session: requests.Session | None = None, lookback_days: int = 7
) -> str | None:
    """Find the newest dated holdings CSV by building its URL directly.

    Since Oct 2026 the US fund pages no longer carry the CSV link in their HTML
    and rate-limit (429) CI runners, but the assets CDN still serves
    ``<slug>_full-holdings_YYYYMMDD.csv`` for each as-of date. Missing dates
    return 404, so walk back from today until one exists.
    """
    m = _US_SLUG_RE.search(fund_page_url or "")
    if not m:
        return None
    slug = m.group(1).lower()
    sess = session or make_session()
    today = datetime.now(timezone.utc).date()
    for back in range(lookback_days + 1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:  # no as-of files for weekends
            continue
        url = _ASSET_URL.format(slug=slug, ymd=d.strftime("%Y%m%d"))
        try:
            resp = sess.get(url, timeout=DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            logger.warning("Global X: probe %s failed: %s", url, exc)
            continue
        if resp.status_code == 200 and not looks_like_html(resp.text):
            return url
    return None


def _discover(fund_page_url: str, *, session: requests.Session | None = None) -> str | None:
    if _is_eu(fund_page_url):
        return _eu_csv_url(fund_page_url)
    probed = _probe_dated_csv(fund_page_url, session=session)
    if probed:
        return probed
    html = http_get(fund_page_url, session=session, retries=1).text
    m = _CSV_LINK_RE.search(html)
    if m:
        return m.group(0)
    logger.warning("Global X: no CSV link found on %s", fund_page_url)
    return None


class GlobalXParser(HoldingsAdapter):
    @property
    def source_name(self) -> str:
        return "web_csv"

    def fetch_holdings(
        self,
        etf_ticker: str,
        fund_page_url: str | None = None,
        csv_url: str | None = None,
        *,
        session: requests.Session | None = None,
        **_,
    ) -> list[dict]:
        url = csv_url or resolve_csv_url(etf_ticker, fund_page_url, _discover, session=session)
        text = http_get(url, session=session).text
        if looks_like_html(text):
            raise HoldingsFetchError(f"[{etf_ticker}] Global X returned HTML, not CSV ({url})")

        is_eu = _is_eu(url) or _is_eu(fund_page_url)
        tokens = ["ticker", "name", "isin"] if is_eu else (
            ["ticker", "name", "weight"] if "weight" in text.lower() else ["ticker", "name", "net"]
        )
        header_idx = find_header_index(text.splitlines(), tokens)
        records = csv_records(text, header_idx)
        if not records:
            raise HoldingsFetchError(f"[{etf_ticker}] Global X CSV had no data rows ({url})")

        as_of = extract_as_of_date(text)
        holdings = []
        for r in records:
            holdings.append(
                {
                    "constituent_ticker": pick(r, "Ticker", "TICKER"),
                    "constituent_name": pick(r, "Name", "NAME"),
                    "isin": pick(r, "ISIN"),
                    "sedol": pick(r, "SEDOL"),
                    "shares_held": pick(r, "Shares Held", "SHARES_HELD", "Shares"),
                    "market_value": pick(r, "Market Value ($)", "MARKET_VALUE", "Market Value"),
                    "weight_pct": pick(r, "% of Net Assets", "NET_ASSETS", "Weight (%)", "Weight"),
                    "currency": "USD",
                    "sector": pick(r, "Sector"),
                    "country": pick(r, "Country", "COUNTRY"),
                    "asset_class": pick(r, "Asset Class", "SecurityType", "Security Type"),
                    "as_of_date": (pick(r, "AS_OF_DATE") if is_eu else None) or as_of,
                }
            )
        return holdings
