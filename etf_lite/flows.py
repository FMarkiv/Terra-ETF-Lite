"""Traded value and AUD conversion for delta rows.

Value Δ (delta_market_value) mixes two things: shares the fund actually traded,
and the price move on shares it already held. Traded value isolates the first:

    traded_value = delta_shares x average of the two snapshot prices

where a snapshot price is market_value / shares_held. An addition is the whole
new position (curr value); a removal is the whole exited one (minus prev value).
No usable price (e.g. a file with no market values) -> None, never a guess.

Every fund reports in its own base currency (USD or AUD), so the *_aud fields
convert at the ECB USD/AUD reference rate for the row's current snapshot date
(latest rate on or before it). If rates can't be fetched the *_aud fields are
None and the build carries on.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import requests

from .universe import fund_currency

logger = logging.getLogger(__name__)

REPORT_CCY = "AUD"
_FX_URL = "https://api.frankfurter.dev/v1/{start}..{end}"


def _price(mv, shares):
    if not mv or not shares:
        return None
    return mv / shares


def traded_value(r: dict) -> float | None:
    dt = r.get("delta_type")
    if dt == "addition":
        return r.get("curr_market_value") or None
    if dt == "removal":
        mv = r.get("prev_market_value")
        return -mv if mv else None
    pxs = [p for p in (_price(r.get("prev_market_value"), r.get("prev_shares")),
                       _price(r.get("curr_market_value"), r.get("curr_shares"))) if p]
    if not pxs:
        return None
    return (r.get("delta_shares") or 0.0) * (sum(pxs) / len(pxs))


class UsdAud:
    """USD->AUD reference rates for a date range, looked up as-of a date."""

    def __init__(self, rates: dict[date, float]):
        self._days = sorted(rates)
        self._rates = rates

    @classmethod
    def fetch(cls, start: date, end: date, session=None) -> "UsdAud | None":
        url = _FX_URL.format(start=(start - timedelta(days=7)).isoformat(), end=end.isoformat())
        try:
            resp = (session or requests).get(url, params={"base": "USD", "symbols": "AUD"}, timeout=20)
            resp.raise_for_status()
            raw = resp.json()["rates"]
            rates = {date.fromisoformat(d): float(v["AUD"]) for d, v in raw.items()}
        except Exception as e:  # noqa: BLE001 — any failure means "no AUD figures"
            logger.warning("USD/AUD fetch failed (%s); AUD values left empty", e)
            return None
        if not rates:
            logger.warning("USD/AUD fetch returned no rates; AUD values left empty")
            return None
        return cls(rates)

    def rate(self, on: date) -> float | None:
        best = None
        for d in self._days:
            if d > on:
                break
            best = d
        return self._rates[best] if best else None


def _to_date(v):
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def enrich(rows: list[dict], fx: UsdAud | None) -> list[dict]:
    """Add fund_ccy, traded_value, traded_value_aud, delta_market_value_aud."""
    for r in rows:
        ccy = fund_currency(r.get("etf_ticker"))
        tv = traded_value(r)
        on = _to_date(r.get("curr_as_of_date") or r.get("prev_as_of_date"))
        if ccy == REPORT_CCY:
            rate = 1.0
        elif ccy == "USD" and fx is not None and on is not None:
            rate = fx.rate(on)
        else:
            rate = None
        dmv = r.get("delta_market_value")
        r["fund_ccy"] = ccy
        r["traded_value"] = tv
        r["fx_to_aud"] = rate
        r["traded_value_aud"] = tv * rate if (tv is not None and rate) else None
        r["delta_market_value_aud"] = dmv * rate if (dmv is not None and rate) else None
    return rows
