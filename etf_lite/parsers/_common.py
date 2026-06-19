"""Shared utilities for the web-CSV parsers (ported from the full tracker).

Covers HTTP (session with browser-ish UA, retries, timeouts), URL resolution
(manual_urls.yaml -> issuer-specific discovery), and CSV parsing helpers.

Differs from the full tracker only in URL resolution: there is no database, so
the ``etf_universe.holdings_csv_url`` step is dropped. Precedence is now
``config/manual_urls.yaml`` -> issuer discovery from the fund page.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
MANUAL_URLS_PATH = REPO_ROOT / "config" / "manual_urls.yaml"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF = 5


class HoldingsFetchError(Exception):
    """Raised when a parser cannot fetch or parse holdings for an ETF."""


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def http_get(
    url: str,
    *,
    session: requests.Session | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff: int = DEFAULT_BACKOFF,
    headers: dict | None = None,
) -> requests.Response:
    """GET with retry/backoff. Raises :class:`HoldingsFetchError` on failure."""
    sess = session or make_session()
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = sess.get(url, timeout=timeout, headers=headers)
            resp.raise_for_status()
            return resp
        except Exception as exc:  # noqa: BLE001 - re-raised as HoldingsFetchError
            last_exc = exc
            logger.warning("GET %s failed (attempt %d/%d): %s", url, attempt, retries, exc)
            if attempt < retries:
                time.sleep(backoff)
    raise HoldingsFetchError(f"Could not fetch {url}: {last_exc}")


def looks_like_html(text: str) -> bool:
    """True if ``text`` is an HTML document (providers serve interstitials with a
    ``text/csv`` content-type — detect that so we don't try to parse a webpage)."""
    head = text.lstrip()[:512].lower()
    return head.startswith("<!doctype html") or head.startswith("<html") or "<head>" in head


# --------------------------------------------------------------------------- #
# URL resolution
# --------------------------------------------------------------------------- #
def load_manual_urls() -> dict:
    """Load operator-maintained URL overrides. Returns ``{etf_ticker: url}``."""
    if not MANUAL_URLS_PATH.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(MANUAL_URLS_PATH.read_text()) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read %s: %s", MANUAL_URLS_PATH, exc)
        return {}
    return {k: v for k, v in (data.get("holdings_csv_urls") or {}).items() if v}


def get_universe_url(etf_ticker: str) -> str | None:
    """No database in the lite build — always falls through to discovery."""
    return None


def resolve_csv_url(
    etf_ticker: str,
    fund_page_url: str | None,
    discover,
    *,
    session: requests.Session | None = None,
) -> str:
    """Resolve the holdings CSV URL: manual override -> issuer discovery."""
    manual = load_manual_urls().get(etf_ticker)
    if manual:
        logger.info("[%s] using manual URL", etf_ticker)
        return manual

    if fund_page_url and discover is not None:
        discovered = discover(fund_page_url, session=session)
        if discovered:
            logger.info("[%s] discovered URL from fund page", etf_ticker)
            return discovered

    raise HoldingsFetchError(
        f"[{etf_ticker}] no holdings CSV URL — add one to config/manual_urls.yaml"
    )


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def find_header_index(lines: list[str], required_tokens: list[str]) -> int:
    """Index of the first line containing *all* required tokens (case-insensitive)."""
    lowered = [t.lower() for t in required_tokens]
    for i, line in enumerate(lines):
        low = line.lower()
        if all(tok in low for tok in lowered):
            return i
    raise HoldingsFetchError(f"Could not find header row containing {required_tokens}")


def csv_records(text: str, header_index: int) -> list[dict]:
    """Parse CSV starting at ``header_index`` into a list of dicts keyed by header."""
    buf = io.StringIO("\n".join(text.splitlines()[header_index:]))
    reader = csv.DictReader(buf)
    records = [r for r in reader if any((v or "").strip() for v in r.values())]
    return records


def pick(record: dict, *aliases: str):
    """Return the first column whose header matches any alias (case-insensitive,
    substring-tolerant). Returns ``None`` if no alias matches."""
    norm = {(k or "").strip().lower(): v for k, v in record.items()}
    for alias in aliases:
        a = alias.strip().lower()
        if a in norm:
            return norm[a]
    for alias in aliases:
        a = alias.strip().lower()
        for k, v in norm.items():
            if a in k:
                return v
    return None


_AS_OF_RE = re.compile(
    r"as of[\s,:]*"
    r"([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}"            # Jun 12, 2026
    r"|\d{1,2}[/-][A-Za-z]{3}[/-]\d{4}"              # 12-Jun-2026
    r"|\d{1,2}/\d{1,2}/\d{4}"                        # 06/12/2026
    r"|\d{4}-\d{2}-\d{2})",                          # 2026-06-12
    re.IGNORECASE,
)


def extract_as_of_date(text: str) -> str | None:
    """Find an 'as of <date>' string anywhere in the document."""
    m = _AS_OF_RE.search(text)
    return m.group(1) if m else None
