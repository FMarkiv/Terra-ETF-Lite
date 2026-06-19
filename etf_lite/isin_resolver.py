"""Ticker / name -> ISIN resolution (ported from the full tracker).

Some providers publish holdings without ISINs (notably Global X US CSVs, which
carry Bloomberg-style tickers and a SEDOL but no ISIN). ISIN is the cross-ETF
join key, so this resolver fills the gaps from a crosswalk built out of the
holdings that *do* carry an ISIN, plus an operator-curated override file.

In this lite build the crosswalk is built from the SAME day's combined holdings
across all 17 ETFs (see :func:`from_rows`) — so an ISIN-rich source (iShares
PICK) resolves the ISIN-less names in a Global X fund within one run, with no
dependency on historical data.

Only *unambiguous* keys resolve: if a ticker/name maps to more than one ISIN,
it is left unresolved (better a gap than a wrong identifier).
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OVERRIDES_PATH = REPO_ROOT / "config" / "isin_overrides.yaml"

# Pure corporate-form suffixes safe to strip for name matching.
_NAME_SUFFIXES = {
    "LTD", "LIMITED", "INC", "INCORPORATED", "CORP", "CORPORATION", "COMPANY",
    "CO", "PLC", "AG", "SA", "NV", "SE", "ASA", "AB", "OYJ", "SAB", "CV", "CLASS",
}

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def normalize_ticker(ticker: str | None) -> str | None:
    if not ticker:
        return None
    t = ticker.strip().upper()
    return t or None


def base_ticker(ticker: str | None) -> str | None:
    """Exchange-stripped root: ``FCX US`` -> ``FCX``, ``BHP.AX`` -> ``BHP``."""
    t = normalize_ticker(ticker)
    if not t:
        return None
    t = t.split()[0].split(".")[0]
    return t or None


def normalize_name(name: str | None) -> str | None:
    if not name:
        return None
    s = re.sub(r"[^A-Z0-9 ]", " ", name.upper())
    toks = [tok for tok in s.split() if tok not in _NAME_SUFFIXES]
    return " ".join(toks) or None


class IsinResolver:
    """Resolves missing ISINs from a crosswalk of ISIN-bearing holdings."""

    def __init__(self, crosswalk_rows=None, overrides: dict | None = None):
        """``crosswalk_rows``: iterable of (ticker, name, country, isin)."""
        self._by_full: dict[str, set] = defaultdict(set)
        self._by_base: dict[str, set] = defaultdict(set)
        self._by_base_country: dict[tuple, set] = defaultdict(set)
        self._by_name: dict[str, set] = defaultdict(set)

        for ticker, name, country, isin in (crosswalk_rows or []):
            if not isin or not _ISIN_RE.match(isin) or isin == "CASH":
                continue
            full = normalize_ticker(ticker)
            base = base_ticker(ticker)
            nm = normalize_name(name)
            ctry = (country or "").strip().upper() or None
            if full:
                self._by_full[full].add(isin)
            if base:
                self._by_base[base].add(isin)
                if ctry:
                    self._by_base_country[(base, ctry)].add(isin)
            if nm:
                self._by_name[nm].add(isin)

        ov = overrides or {}
        self._ov_ticker = {}
        for k, v in (ov.get("by_ticker") or {}).items():
            if ":" in k:  # "TICKER:COUNTRY" form
                tk, ct = k.rsplit(":", 1)
                self._ov_ticker[(normalize_ticker(tk), ct.strip().upper())] = v
            self._ov_ticker[base_ticker(k)] = v
            self._ov_ticker[normalize_ticker(k)] = v
        self._ov_name = {normalize_name(k): v for k, v in (ov.get("by_name") or {}).items()}

        self.stats: dict[str, int] = defaultdict(int)

    # -- construction helpers --------------------------------------------

    @classmethod
    def from_rows(cls, rows, overrides_path: str | Path | None = None):
        """Build a resolver from normalised holding dicts (this run's data)."""
        crosswalk = [
            (r.get("constituent_ticker"), r.get("constituent_name"),
             r.get("country"), r.get("isin"))
            for r in rows
            if r.get("isin") and r["isin"] != "CASH"
        ]
        return cls(crosswalk, load_overrides(overrides_path))

    # -- resolution -------------------------------------------------------

    def resolve(self, ticker=None, name=None, country=None):
        """Return ``(isin, method)`` or ``(None, None)``. Only unambiguous hits."""
        full = normalize_ticker(ticker)
        base = base_ticker(ticker)
        nm = normalize_name(name)
        ctry = (country or "").strip().upper() or None

        # 1) static overrides (most trusted)
        if ctry and (base, ctry) in self._ov_ticker:
            return self._hit(self._ov_ticker[(base, ctry)], "override_ticker_country")
        if full and full in self._ov_ticker:
            return self._hit(self._ov_ticker[full], "override_ticker")
        if base and base in self._ov_ticker:
            return self._hit(self._ov_ticker[base], "override_ticker")
        if nm and nm in self._ov_name:
            return self._hit(self._ov_name[nm], "override_name")

        # 2) crosswalk: full ticker, then (base, country), then base, then name
        for key, table, method in (
            (full, self._by_full, "ticker_full"),
            ((base, ctry) if base and ctry else None, self._by_base_country, "ticker_base_country"),
            (base, self._by_base, "ticker_base"),
            (nm, self._by_name, "name"),
        ):
            if key is None:
                continue
            cands = table.get(key)
            if cands and len(cands) == 1:
                return self._hit(next(iter(cands)), method)
            if cands and len(cands) > 1:
                self.stats["ambiguous"] += 1
        self.stats["unresolved"] += 1
        return None, None

    def _hit(self, isin, method):
        self.stats[method] += 1
        self.stats["resolved"] += 1
        return isin, method

    def __bool__(self):
        return bool(self._by_full or self._by_name or self._ov_ticker or self._ov_name)


def load_overrides(path: str | Path | None = None) -> dict:
    p = Path(path) if path is not None else DEFAULT_OVERRIDES_PATH
    if not p.exists():
        return {}
    try:
        import yaml

        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read ISIN overrides %s: %s", p, exc)
        return {}
