"""SPDR / State Street holdings parser.

ETFs: XME. SPDR publishes daily holdings as XLSX at a stable library URL
templated off the ticker.
"""

from __future__ import annotations

import io
import logging

import requests

from ..base import HoldingsAdapter
from ._common import (
    HoldingsFetchError,
    extract_as_of_date,
    http_get,
    pick,
    resolve_csv_url,
)

logger = logging.getLogger(__name__)

_XLSX_TEMPLATE = (
    "https://www.ssga.com/us/en/intermediary/etfs/library-content/products/"
    "fund-data/etfs/us/holdings-daily-us-en-{ticker}.xlsx"
)


def _discover(fund_page_url: str, *, session: requests.Session | None = None) -> str | None:
    # SPDR URL is templated off the ticker, not scraped — see fetch_holdings.
    return None


class SpdrParser(HoldingsAdapter):
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
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover
            raise HoldingsFetchError("pandas/openpyxl required for SPDR XLSX parsing") from exc

        url = csv_url
        if not url:
            try:
                url = resolve_csv_url(etf_ticker, fund_page_url, _discover, session=session)
            except HoldingsFetchError:
                url = _XLSX_TEMPLATE.format(ticker=etf_ticker.lower())

        content = http_get(url, session=session).content
        try:
            raw = pd.read_excel(io.BytesIO(content), header=None, engine="openpyxl")
        except Exception as exc:  # noqa: BLE001
            raise HoldingsFetchError(f"[{etf_ticker}] could not read SPDR XLSX ({url}): {exc}") from exc

        header_row = None
        for i, row in raw.iterrows():
            cells = [str(c).strip().lower() for c in row.tolist()]
            if "ticker" in cells and any("weight" in c for c in cells):
                header_row = i
                break
        if header_row is None:
            raise HoldingsFetchError(f"[{etf_ticker}] no header row in SPDR XLSX ({url})")

        header = [str(c).strip() for c in raw.iloc[header_row].tolist()]
        body = raw.iloc[header_row + 1 :].copy()
        body.columns = header

        meta_text = " ".join(
            str(c) for c in raw.iloc[:header_row].values.flatten() if str(c) != "nan"
        )
        as_of = extract_as_of_date(meta_text)

        holdings = []
        for _, row in body.iterrows():
            rec = {k: row[k] for k in header}
            ticker = pick(rec, "Ticker")
            name = pick(rec, "Name")
            if (ticker is None or str(ticker) == "nan") and (name is None or str(name) == "nan"):
                continue
            holdings.append(
                {
                    "constituent_ticker": ticker,
                    "constituent_name": name,
                    "isin": pick(rec, "ISIN"),
                    "sedol": pick(rec, "SEDOL"),
                    "shares_held": pick(rec, "Shares Held", "Shares"),
                    "market_value": pick(rec, "Market Value"),
                    "weight_pct": pick(rec, "Weight"),
                    "currency": pick(rec, "Local Currency", "Currency"),
                    "sector": pick(rec, "Sector"),
                    "country": pick(rec, "Country"),
                    "as_of_date": as_of,
                }
            )
        if not holdings:
            raise HoldingsFetchError(f"[{etf_ticker}] SPDR XLSX had no data rows ({url})")
        return holdings
