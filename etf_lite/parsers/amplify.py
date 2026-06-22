"""Amplify holdings parser.

ETFs: SILJ. Amplify publishes a single master feed covering all its funds; we
filter rows to ``Account == etf_ticker``. CUSIP only (no ISIN) — resolved later.
"""

from __future__ import annotations

import logging
import re

import requests

from ..base import HoldingsAdapter
from ._common import (
    HoldingsFetchError,
    csv_records,
    find_header_index,
    http_get,
    load_manual_urls,
    looks_like_html,
    pick,
)

logger = logging.getLogger(__name__)

_FEED_RE = re.compile(r"https?://[^\"'\s]*AmplifyWeb\.[^\"'\s]*Holdings\.csv", re.I)
_MASTER_FEED = "https://amplifyetfs.com/wp-content/uploads/feeds/AmplifyWeb.40XL.XL_Holdings.csv"


def _discover(fund_page_url: str, *, session: requests.Session | None = None) -> str | None:
    html = http_get(fund_page_url, session=session).text
    m = _FEED_RE.search(html)
    return m.group(0) if m else None


class AmplifyParser(HoldingsAdapter):
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
        url = csv_url or load_manual_urls().get(etf_ticker)
        if not url and fund_page_url:
            url = _discover(fund_page_url, session=session)
        url = url or _MASTER_FEED

        text = http_get(url, session=session).text
        if looks_like_html(text):
            raise HoldingsFetchError(f"[{etf_ticker}] Amplify feed returned HTML ({url})")

        header_idx = find_header_index(text.splitlines(), ["account", "stockticker", "securityname"])
        records = csv_records(text, header_idx)
        mine = [r for r in records if (pick(r, "Account") or "").strip().upper() == etf_ticker.upper()]
        if not mine:
            raise HoldingsFetchError(
                f"[{etf_ticker}] no rows for Account={etf_ticker} in Amplify feed ({url})"
            )

        return [
            {
                "constituent_ticker": pick(r, "StockTicker", "Ticker"),
                "constituent_name": pick(r, "SecurityName", "Name"),
                "isin": pick(r, "ISIN"),
                "sedol": pick(r, "SEDOL"),
                "shares_held": pick(r, "Shares"),
                "market_value": pick(r, "MarketValue", "Market Value"),
                "weight_pct": pick(r, "Weightings", "Weight"),
                "currency": "USD",
                "sector": pick(r, "Sector"),
                "country": pick(r, "Country"),
                "asset_class": pick(r, "SecurityType", "Asset Class", "Security Type"),
                "as_of_date": pick(r, "Date"),
            }
            for r in mine
        ]
