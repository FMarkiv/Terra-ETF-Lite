"""Parser registry — dispatch by issuer family."""

from __future__ import annotations

from .amplify import AmplifyParser
from .betashares import BetaSharesParser
from .globalx import GlobalXParser
from .ishares import ISharesParser
from .spdr import SpdrParser
from .vaneck import VanEckParser

_PARSERS = {
    "amplify": AmplifyParser,
    "betashares": BetaSharesParser,
    "globalx": GlobalXParser,
    "ishares": ISharesParser,
    "spdr": SpdrParser,
    "vaneck": VanEckParser,
}


def get_parser(issuer: str):
    """Return a parser instance for an issuer key (e.g. ``'vaneck'``)."""
    try:
        return _PARSERS[issuer]()
    except KeyError:
        raise ValueError(f"No parser for issuer {issuer!r}") from None
