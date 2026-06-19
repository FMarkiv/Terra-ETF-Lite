"""BetaShares holdings parser.

ETFs: MNRS, URNM-ASX. Tries a CSV download link, then scrapes the holdings
table with BeautifulSoup. AUD-hedged wrappers around global indexes.
"""

from __future__ import annotations

import logging
import re

import requests

from ..base import HoldingsAdapter
from ._common import (
    HoldingsFetchError,
    csv_records,
    extract_as_of_date,
    find_header_index,
    http_get,
    looks_like_html,
    pick,
    resolve_csv_url,
)

logger = logging.getLogger(__name__)

_CSV_LINK_RE = re.compile(
    r"""(?:href|src)=["']([^"']*(?:holding|portfolio)[^"']*\.csv[^"']*)["']""",
    re.IGNORECASE,
)


def _discover(fund_page_url: str, *, session: requests.Session | None = None) -> str | None:
    html = http_get(fund_page_url, session=session).text
    m = _CSV_LINK_RE.search(html)
    if m:
        link = m.group(1)
        if not link.startswith("http"):
            from urllib.parse import urljoin

            link = urljoin(fund_page_url, link)
        return link
    return None  # signal to fall through to table scraping


_DATE_LINE_RE = re.compile(r"^\s*date\s*,\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", re.IGNORECASE | re.MULTILINE)


def _betashares_date(text: str) -> str | None:
    m = _DATE_LINE_RE.search(text)
    return m.group(1) if m else None


def _scrape_table(html: str, fund_page_url: str) -> list[dict]:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover
        raise HoldingsFetchError("beautifulsoup4 required for BetaShares scraping") from exc

    soup = BeautifulSoup(html, "html.parser")
    as_of = extract_as_of_date(soup.get_text(" "))

    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        low = [h.lower() for h in headers]
        if not headers or not any("name" in h or "ticker" in h or "security" in h for h in low):
            continue
        if not any("weight" in h or "%" in h for h in low):
            continue
        rows = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) != len(headers):
                continue
            rec = dict(zip(headers, cells))
            rows.append(
                {
                    "constituent_ticker": pick(rec, "Ticker", "Code", "ASX Code"),
                    "constituent_name": pick(rec, "Name", "Security", "Holding"),
                    "isin": pick(rec, "ISIN"),
                    "sedol": pick(rec, "SEDOL"),
                    "shares_held": pick(rec, "Shares", "Units", "Shares Held"),
                    "market_value": pick(rec, "Market Value", "Value"),
                    "weight_pct": pick(rec, "Weight", "Weight (%)", "% Weight", "% of Net Assets"),
                    "currency": pick(rec, "Currency"),
                    "sector": pick(rec, "Sector"),
                    "country": pick(rec, "Country"),
                    "as_of_date": as_of,
                }
            )
        if rows:
            return rows
    raise HoldingsFetchError(
        f"BetaShares: no static holdings table found at {fund_page_url} "
        f"(likely JS-rendered) — add a manual URL."
    )


class BetaSharesParser(HoldingsAdapter):
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
        url = csv_url
        if not url:
            try:
                url = resolve_csv_url(etf_ticker, fund_page_url, _discover, session=session)
            except HoldingsFetchError:
                url = None

        if url:
            text = http_get(url, session=session).text
            if not looks_like_html(text):
                header_idx = find_header_index(text.splitlines(), ["ticker", "name", "weight"])
                records = csv_records(text, header_idx)
                as_of = _betashares_date(text) or extract_as_of_date(text)
                return [
                    {
                        "constituent_ticker": pick(r, "Ticker", "Code"),
                        "constituent_name": pick(r, "Name", "Security"),
                        "isin": pick(r, "ISIN"),
                        "sedol": pick(r, "SEDOL"),
                        "shares_held": pick(r, "Shares/Units (#)", "Shares", "Units"),
                        "market_value": pick(r, "Market Value (AUD)", "Market Value", "Value"),
                        "weight_pct": pick(r, "Weight (%)", "Weight", "% of Net Assets"),
                        "currency": "AUD",
                        "sector": pick(r, "Sector"),
                        "country": pick(r, "Country"),
                        "as_of_date": as_of,
                    }
                    for r in records
                ]

        if not fund_page_url:
            raise HoldingsFetchError(f"[{etf_ticker}] no BetaShares URL or fund page to scrape")
        html = http_get(fund_page_url, session=session).text
        return _scrape_table(html, fund_page_url)
