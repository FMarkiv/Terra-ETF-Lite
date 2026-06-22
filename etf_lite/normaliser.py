"""Post-parse normalisation shared by every web-CSV parser (ported verbatim).

Parsers return best-effort rows; this enforces the canonical-schema invariants:

* ``weight_pct`` is put on a 0-100 scale (detected per-batch from the sum).
* Tickers / names are whitespace-stripped.
* ISINs are format-validated; malformed values are nulled.
* Cash / money-market lines are tagged with the sentinel ISIN ``CASH``.
* ``as_of_date`` strings are coerced to :class:`datetime.date`.
* Missing fields become ``None`` rather than raising.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

logger = logging.getLogger(__name__)

from .base import CANONICAL_FIELDS
from .classify import CASH, classify_instrument

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

# Accepted date formats seen across providers (and ISO).
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%d-%b-%Y",
    "%d-%b-%y",       # VanEck AU: 15-Jun-26
    "%d %b %Y",       # VanEck UK: 31 May 2026
    "%b %d, %Y",
    "%Y%m%d",
)


def is_valid_isin(value: str) -> bool:
    """True if ``value`` matches the ISIN format (2 alpha + 9 alnum + check digit)."""
    return bool(_ISIN_RE.match(value or ""))


_STR_PLACEHOLDERS = {"", "-", "--", "n/a", "na", "nan", "none", "null"}


def _clean_str(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return None if s.lower() in _STR_PLACEHOLDERS else s


def _to_float(value) -> float | None:
    """Parse a numeric value that may carry commas, currency symbols, %, or
    parentheses-for-negative. Returns ``None`` for blanks / non-numerics."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return None if f != f else f  # NaN (pandas empty cell) -> None
    s = str(value).strip()
    if s.lower() == "nan":
        return None
    if s in ("", "-", "--", "N/A", "n/a", "NA"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace("%", "").replace("$", "").strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return -f if neg else f


def _coerce_date(value):
    """Coerce a date/datetime/string into a :class:`datetime.date` (or None)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    logger.warning("Could not parse as_of_date %r", value)
    return None


def _detect_fractional(weights: list[float]) -> bool:
    """Decide whether a batch of weights is on a 0-1 fractional scale."""
    nums = [w for w in weights if w is not None]
    if not nums:
        return False
    total = sum(nums)
    return 0 < total <= 1.5 and max(nums) <= 1.0


def normalise_holdings(
    rows: list[dict],
    etf_ticker: str | None = None,
    *,
    default_as_of_date: date | None = None,
    isin_resolver=None,
) -> list[dict]:
    """Normalise a batch of parsed rows into canonical-schema dicts."""
    ctx = f"[{etf_ticker}] " if etf_ticker else ""

    # First pass: clean strings/numbers, coerce dates.
    cleaned: list[dict] = []
    for raw in rows:
        row = {field: raw.get(field) for field in CANONICAL_FIELDS}
        row["constituent_ticker"] = _clean_str(row["constituent_ticker"])
        row["constituent_name"] = _clean_str(row["constituent_name"])
        row["isin"] = _clean_str(row["isin"])
        row["sedol"] = _clean_str(row["sedol"])
        row["currency"] = _clean_str(row["currency"])
        row["sector"] = _clean_str(row["sector"])
        row["country"] = _clean_str(row["country"])
        row["asset_class"] = _clean_str(row["asset_class"])
        row["shares_held"] = _to_float(row["shares_held"])
        row["market_value"] = _to_float(row["market_value"])
        row["weight_pct"] = _to_float(row["weight_pct"])
        row["as_of_date"] = _coerce_date(row["as_of_date"]) or default_as_of_date

        # ISIN: uppercase, then format-validate. The CASH sentinel is preserved.
        if row["isin"]:
            iv = row["isin"].upper()
            if iv == "CASH" or is_valid_isin(iv):
                row["isin"] = iv
            else:
                logger.warning("%sdropping malformed ISIN %r", ctx, row["isin"])
                row["isin"] = None
        cleaned.append(row)

    # Second pass: per-batch weight scale detection.
    if _detect_fractional([r["weight_pct"] for r in cleaned]):
        logger.info("%sweights look fractional (0-1) — scaling to 0-100", ctx)
        for r in cleaned:
            if r["weight_pct"] is not None:
                r["weight_pct"] *= 100.0

    # Third pass: drop non-holding rows, classify instrument type, tag cash,
    # warn on partial rows.
    result: list[dict] = []
    for r in cleaned:
        has_identity = r["constituent_ticker"] or r["isin"]
        has_quantity = (
            r["weight_pct"] is not None
            or r["shares_held"] is not None
            or r["market_value"] is not None
        )
        if not has_identity and not has_quantity:
            logger.debug("%sdropping non-holding row: %r", ctx, (r["constituent_name"] or "")[:50])
            continue

        # Single source of truth for cash/FX/derivative/equity (see classify.py).
        r["instrument_type"] = classify_instrument(r)
        if r["instrument_type"] == CASH and not (r["isin"] and r["isin"] != "CASH"):
            r["isin"] = "CASH"  # sentinel kept for the cross-ETF / legacy filters
        if not has_identity:
            logger.warning("%srow has a quantity but no ticker/ISIN: %r", ctx,
                           (r["constituent_name"] or "")[:50])
        result.append(r)

    # Fourth pass: resolve still-missing ISINs from the crosswalk (if provided).
    if isin_resolver is not None:
        n_resolved = 0
        for r in result:
            if r["isin"] is None and (r["constituent_ticker"] or r["constituent_name"]):
                isin, _method = isin_resolver.resolve(
                    r["constituent_ticker"], r["constituent_name"], r["country"]
                )
                if isin:
                    r["isin"] = isin
                    n_resolved += 1
        if n_resolved:
            logger.info("%sresolved %d missing ISIN(s) from crosswalk", ctx, n_resolved)

    return result
