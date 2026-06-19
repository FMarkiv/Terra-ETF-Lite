"""Abstract adapter interface + canonical schema (ported verbatim).

Parsers return best-effort rows in the canonical schema below; the normaliser
(:mod:`etf_lite.normaliser`) enforces the invariants afterwards (0-100 weight
scale, ISIN validation, CASH tagging, date coercion).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

CANONICAL_FIELDS: tuple[str, ...] = (
    "constituent_ticker",
    "constituent_name",
    "isin",
    "sedol",
    "shares_held",
    "market_value",
    "weight_pct",
    "currency",
    "sector",
    "country",
    "as_of_date",
)


class HoldingsAdapter(ABC):
    """Base class for all holdings sources."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Returns ``'web_csv'`` — stored in the ``source`` column."""

    @abstractmethod
    def fetch_holdings(self, etf_ticker: str, *args, **kwargs) -> list[dict]:
        """Fetch holdings for one ETF and return canonical-schema dicts.

        Implementations must raise an informative exception on failure
        (network error, unparseable page, no data) — never fail silently.
        """
