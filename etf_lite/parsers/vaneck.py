"""VanEck holdings parser.

ETFs (lite): GDX, GDXJ, REMX (US), GDX-ASX (AU) — full holdings.
(UK UCITS GDX.L/GDXJ.L/WMIN are top-10-only on the web and excluded from lite.)

VanEck renders holdings client-side but the data comes from a JSON endpoint:
the fund page embeds ``<ve-holdingsblock data-blockid=.. data-pageid=..>`` whose
ids drive ``/Main/HoldingsBlock/GetContent/`` returning ``data.Holdings``.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import requests

from ..base import HoldingsAdapter
from ._common import (
    HoldingsFetchError,
    csv_records,
    extract_as_of_date,
    find_header_index,
    http_get,
    load_manual_urls,
    looks_like_html,
    pick,
)

logger = logging.getLogger(__name__)

_BLOCK_RE = re.compile(r"<ve-holdingsblock\b[^>]*>", re.I)
_GETCONTENT = (
    "https://www.vaneck.com/Main/HoldingsBlock/GetContent/"
    "?blockid={blockid}&pageid={pageid}&ticker={ticker}"
    "&reactlang=en&reactctr={ctr}&epieditmode=false&latest=false&contextmode=Default"
)
_MIN_FULL_HOLDINGS = 11


def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf'data-{name}="([^"]*)"', tag)
    return m.group(1) if m else None


def _reactctr(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.endswith(".com.au"):
        return "au"
    if "/uk/" in url.lower():
        return "uk"
    return "us"


def _map_json_holdings(holdings: list[dict], as_of) -> list[dict]:
    out = []
    for h in holdings:
        out.append(
            {
                "constituent_ticker": h.get("Label") or h.get("HoldingTicker"),
                "constituent_name": h.get("HoldingName"),
                "isin": h.get("ISIN"),
                "sedol": h.get("SEDOL"),
                "shares_held": h.get("Shares"),
                "market_value": h.get("MV"),
                "weight_pct": h.get("Weight"),
                "currency": h.get("CurrencyCode"),
                "sector": h.get("Sector"),
                "country": h.get("Country"),
                "as_of_date": as_of,
            }
        )
    return out


class VanEckParser(HoldingsAdapter):
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
        manual = csv_url or load_manual_urls().get(etf_ticker)
        if manual:
            return self._parse_manual_csv(etf_ticker, manual, session)

        if not fund_page_url:
            raise HoldingsFetchError(f"[{etf_ticker}] no fund page URL for VanEck")

        html = http_get(fund_page_url, session=session).text
        tag = _BLOCK_RE.search(html)
        if not tag:
            raise HoldingsFetchError(
                f"[{etf_ticker}] no <ve-holdingsblock> on VanEck page — layout changed "
                f"or fully JS-only; add a manual URL"
            )
        blockid, pageid = _attr(tag.group(0), "blockid"), _attr(tag.group(0), "pageid")
        ticker = _attr(tag.group(0), "ticker") or etf_ticker.replace(".L", "").replace("-ASX", "")
        ctr = _reactctr(fund_page_url)
        api = _GETCONTENT.format(blockid=blockid, pageid=pageid, ticker=ticker, ctr=ctr)

        resp = http_get(api, session=session, headers={"X-Requested-With": "XMLHttpRequest"})
        try:
            data = resp.json()["data"]
        except Exception as exc:  # noqa: BLE001
            raise HoldingsFetchError(f"[{etf_ticker}] VanEck GetContent non-JSON ({api}): {exc}") from exc

        holdings = data.get("Holdings") or []
        if data.get("IsTopTen") or 0 < len(holdings) < _MIN_FULL_HOLDINGS:
            raise HoldingsFetchError(
                f"[{etf_ticker}] VanEck web exposes only top-{len(holdings)} holdings "
                f"(UK UCITS disclose full holdings monthly)"
            )
        if not holdings:
            raise HoldingsFetchError(f"[{etf_ticker}] VanEck returned no holdings ({api})")

        return _map_json_holdings(holdings, data.get("AsOfDate"))

    def _parse_manual_csv(self, etf_ticker, url, session) -> list[dict]:
        text = http_get(url, session=session).text
        if looks_like_html(text):
            raise HoldingsFetchError(f"[{etf_ticker}] manual VanEck URL returned HTML ({url})")
        header_idx = find_header_index(text.splitlines(), ["ticker", "name"])
        records = csv_records(text, header_idx)
        as_of = extract_as_of_date(text)
        return [
            {
                "constituent_ticker": pick(r, "Ticker", "Holding Ticker"),
                "constituent_name": pick(r, "Name", "Holding Name", "Security"),
                "isin": pick(r, "ISIN"),
                "sedol": pick(r, "SEDOL"),
                "shares_held": pick(r, "Shares", "Shares Held"),
                "market_value": pick(r, "Market Value", "Market Value (USD)"),
                "weight_pct": pick(r, "% of Net Assets", "Weighting", "Weight (%)", "Weight"),
                "currency": pick(r, "Currency", "Local Currency"),
                "sector": pick(r, "Sector"),
                "country": pick(r, "Country"),
                "as_of_date": as_of,
            }
            for r in records
        ]
