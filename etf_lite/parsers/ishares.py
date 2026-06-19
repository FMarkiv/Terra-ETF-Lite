"""iShares (BlackRock) holdings parser.

ETFs: PICK, RING, SLVP (US); IS0E (UK UCITS).

The ``.ajax?fileType=csv`` download is IP-gated for datacenter clients, but the
React product page's JSON API is not gated and works with plain requests. We try
JSON first, then fall back to the ``.ajax`` CSV (works from residential IPs).
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
    load_manual_urls,
    looks_like_html,
    pick,
)

logger = logging.getLogger(__name__)

_PRODUCT_ID_RE = re.compile(r"/products/(\d+)/")
_AJAX_GENERATOR = "1467271812596.ajax"
_PRODUCT_DATA = (
    "https://www.ishares.com/varnish-api/blk-one01-product-data/product-data/api/v2/"
    "get-product-data?appSubType=ISHARES&appType=PRODUCT_PAGE&component=holdings.all"
    "&locale={locale}&portfolioId={pid}&targetSite={site}&userType=individual"
    "&excludeContent=true&includeConfig=true"
)

_FIELD_MAP = {
    "ticker": "constituent_ticker",
    "issueName": "constituent_name",
    "isin": "isin",
    "sedol": "sedol",
    "unitsHeld": "shares_held",
    "marketValue": "market_value",
    "holdingPercent": "weight_pct",
    "currencyCode": "currency",
    "sectorName": "sector",
    "countryOfRisk": "country",
}


def _region(url: str) -> tuple[str, str]:
    u = (url or "").lower()
    if "/uk/" in u:
        return "uk-ishares", "en_GB"
    return "us-ishares", "en_US"


def _product_id(url: str) -> str | None:
    m = _PRODUCT_ID_RE.search(url or "")
    return m.group(1) if m else None


def _parse_product_data(j: dict) -> list[dict]:
    dp = (
        j.get("componentsByNameMap", {})
        .get("holdings", {})
        .get("containersByNameMap", {})
        .get("all", {})
        .get("dataPointsByNameMap", {})
    )
    if not dp or "ticker" not in dp:
        raise HoldingsFetchError("iShares JSON missing holdings dataPoints")

    def col(name):
        v = (dp.get(name) or {}).get("formattedValue")
        return v if isinstance(v, list) else []

    columns = {canon: col(field) for field, canon in _FIELD_MAP.items()}
    n = len(columns["constituent_ticker"])

    asof_raw = (dp.get("asOfDate") or {}).get("formattedValue")
    as_of = asof_raw[0] if isinstance(asof_raw, list) and asof_raw else asof_raw

    rows = []
    for i in range(n):
        row = {canon: (vals[i] if i < len(vals) else None) for canon, vals in columns.items()}
        row["as_of_date"] = as_of
        rows.append(row)
    return rows


class ISharesParser(HoldingsAdapter):
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
            return self._parse_csv(etf_ticker, manual, session)

        pid = _product_id(fund_page_url or "")
        if not pid:
            raise HoldingsFetchError(f"[{etf_ticker}] no product id in iShares URL")

        # 1) JSON product-data API (ungated, requests-only).
        site, locale = _region(fund_page_url)
        api = _PRODUCT_DATA.format(pid=pid, site=site, locale=locale)
        try:
            resp = http_get(api, session=session, headers={"Accept": "application/json"})
            if "json" in resp.headers.get("content-type", ""):
                rows = _parse_product_data(resp.json())
                if rows:
                    return rows
            logger.warning("[%s] iShares JSON API returned no holdings; trying .ajax", etf_ticker)
        except HoldingsFetchError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] iShares JSON API failed (%s); trying .ajax", etf_ticker, exc)

        # 2) Fallback: the .ajax CSV (works from residential IP).
        ajax = (
            f"{fund_page_url.rstrip('/')}/{_AJAX_GENERATOR}"
            f"?fileType=csv&fileName={etf_ticker}_holdings&dataType=fund"
        )
        text = http_get(ajax, session=session).text
        if looks_like_html(text):
            raise HoldingsFetchError(
                f"[{etf_ticker}] iShares JSON API empty and .ajax is IP-gated (HTML). "
                f"Run from a residential IP or add a URL to config/manual_urls.yaml."
            )
        return self._parse_csv_text(text)

    # ------------------------------------------------------------------ #
    def _parse_csv(self, etf_ticker, url, session) -> list[dict]:
        text = http_get(url, session=session).text
        if looks_like_html(text):
            raise HoldingsFetchError(f"[{etf_ticker}] manual iShares URL returned HTML ({url})")
        return self._parse_csv_text(text)

    @staticmethod
    def _parse_csv_text(text: str) -> list[dict]:
        header_idx = find_header_index(text.splitlines(), ["ticker", "name", "weight"])
        records = csv_records(text, header_idx)
        as_of = extract_as_of_date(text)
        return [
            {
                "constituent_ticker": pick(r, "Ticker"),
                "constituent_name": pick(r, "Name"),
                "isin": pick(r, "ISIN"),
                "sedol": pick(r, "SEDOL"),
                "shares_held": pick(r, "Shares"),
                "market_value": pick(r, "Market Value"),
                "weight_pct": pick(r, "Weight (%)", "Weight"),
                "currency": pick(r, "Market Currency", "Currency"),
                "sector": pick(r, "Sector"),
                "country": pick(r, "Location", "Country"),
                "as_of_date": as_of,
            }
            for r in records
        ]
