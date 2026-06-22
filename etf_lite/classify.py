"""Central instrument classification — the single source of truth for whether a
holdings row is a real, trackable equity position or a non-economic sleeve
(cash, FX, derivative/hedge, fixed income, residual).

Two tiers, most-reliable first:

1. **Provider asset-class label** — when a parser captured the issuer's own
   ``asset_class`` (iShares "Asset Class", VanEck ``SecurityType`` …). The issuer
   knows whether a row is a future or a stock; trust it.
2. **Heuristic fallback** — name/ticker keywords plus the zero-economic shape —
   used when the provider gives nothing (SPDR XLSX, BetaShares scrape, and
   legacy snapshots written before this column existed).

Everything downstream (delta SQL, cross-ETF, dashboard) filters on the resulting
``instrument_type``, so the rule lives in exactly one place and is unit-tested.
"""

from __future__ import annotations

# Canonical instrument types.
EQUITY = "equity"
CASH = "cash"
FX = "fx"
DERIVATIVE = "derivative"
FIXED_INCOME = "fixed_income"
RESIDUAL = "residual"

# Only equities are "flow" — the rest are sleeves/hedges/residuals.
TRACKABLE_TYPES = frozenset({EQUITY})

# --- tier 1: provider asset-class label -> canonical type --------------------
# First matching substring wins, so order non-equity before equity. "Cash and/or
# Derivatives" (iShares) matches 'cash' first — fine, both are non-trackable.
_PROVIDER_CLASS_RULES: tuple[tuple[str, str], ...] = (
    ("money market", CASH),
    ("cash", CASH),
    ("foreign exchange", FX),
    ("currency", FX),
    ("forward", DERIVATIVE),
    ("future", DERIVATIVE),
    ("swap", DERIVATIVE),
    ("option", DERIVATIVE),
    ("warrant", DERIVATIVE),
    ("derivative", DERIVATIVE),
    ("fixed income", FIXED_INCOME),
    ("treasury", FIXED_INCOME),
    ("bond", FIXED_INCOME),
    ("equit", EQUITY),     # equity / equities
    ("common stock", EQUITY),
    ("ordinary share", EQUITY),
    ("stock", EQUITY),
    ("share", EQUITY),
    ("depositary", EQUITY),  # ADR/GDR
    ("reit", EQUITY),
)

# --- tier 2: heuristic fallback ---------------------------------------------
# Cash / money-market / residual markers (matched in name+ticker).
_CASH_MARKERS = (
    "cash", "money market", "money mkt", "net other asset", "other net asset",
    "net current asset", "liquidity fund", "margin", "collateral", "repurchase",
    "repo ", "payable", "receivable", "accrued", "net assets", "subscription",
    "redemption", "dividend pending",
)

# Derivative / hedge markers (FX forwards, index futures, swaps, options …).
_DERIVATIVE_MARKERS = (
    "future", " fut ", "fut.", "fwd", "forward", "swap", "option", " call ",
    " put ", "p-note", "p note", "participat", "warrant", "tba", "to be announced",
    "contract for diff", " cfd", "index fut", "hedge",
)

# ISO currency codes that appear as bare tickers on FX cash sleeves. Matched only
# when the row has no ISIN, so a real ticker that merely contains a code
# (e.g. ``EUR AU`` = European Lithium) is never misclassified.
_FX_CURRENCY_CODES = frozenset({
    "AUD", "CAD", "GBP", "HKD", "JPY", "ZAR", "USD", "EUR", "CHF", "NZD", "SGD",
    "SEK", "NOK", "DKK", "CNY", "CNH", "KRW", "BRL", "MXN", "IDR", "INR", "PLN",
    "TRY", "PHP", "THB", "MYR", "TWD", "SAR", "AED", "ILS", "CLP", "PEN", "COP",
    "HUF", "CZK", "RUB",
})

# Currency *names* (providers that spell them out: "CANADIAN DOLLAR", "SAUDI
# RIYAL", "KOREAN WON"). Matched only when the row has no ISIN.
_FX_CURRENCY_NAMES = (
    "dollar", "euro", "yen", "sterling", "pound", "franc", "rand", "yuan",
    "renminbi", "riyal", "won", "krona", "krone", "kroner", "peso", "rupee",
    "ringgit", "baht", "real", "ruble", "rouble", "zloty", "lira", "dirham",
    "shekel", "koruna", "forint", "rupiah", "dinar", "rupee",
)


def _norm(value) -> str:
    return str(value or "").strip().lower()


def classify_asset_class(asset_class) -> str | None:
    """Map a raw provider asset-class label to a canonical type, or ``None`` if
    the label is empty or unrecognised (caller then falls back to heuristics)."""
    ac = _norm(asset_class)
    if not ac:
        return None
    for needle, kind in _PROVIDER_CLASS_RULES:
        if needle in ac:
            return kind
    return None


def classify_instrument(row: dict) -> str:
    """Classify a normalised holdings row into a canonical ``instrument_type``.

    Provider label wins when present and recognised; otherwise heuristics on the
    name/ticker and the economic shape decide. Defaults to ``equity`` only when
    nothing flags the row as a sleeve/hedge/residual.
    """
    provider = classify_asset_class(row.get("asset_class"))
    if provider is not None:
        return provider

    name = row.get("constituent_name") or ""
    ticker = row.get("constituent_ticker") or ""
    isin = row.get("isin")
    has_isin = bool(isin) and isin != CASH.upper()  # 'CASH' sentinel is not a real ISIN
    blob = f"{name} {ticker}".lower()

    if any(m in blob for m in _CASH_MARKERS):
        return CASH
    if any(m in blob for m in _DERIVATIVE_MARKERS):
        return DERIVATIVE
    if not has_isin:
        if ticker.strip().upper() in _FX_CURRENCY_CODES:
            return FX
        if any(n in blob for n in _FX_CURRENCY_NAMES):
            return FX

    has_identity = bool(ticker.strip()) or has_isin
    if not has_identity:
        return RESIDUAL

    # Zero-economic rows (no weight and no value) are cash-equitization index
    # futures that only churn add/remove pairs on contract roll — not real flow.
    weight = row.get("weight_pct") or 0
    mvalue = row.get("market_value") or 0
    if weight == 0 and mvalue == 0:
        return DERIVATIVE

    return EQUITY


def is_trackable(row: dict) -> bool:
    """True if the row is a real, trackable equity holding."""
    return classify_instrument(row) in TRACKABLE_TYPES
