"""Delta-engine SQL — the analytical core (ported from the full tracker).

Two ideas underpin the SQL:

* **Latest-vs-prior, not literally today-vs-yesterday.** ``DENSE_RANK`` over the
  distinct ``as_of_date`` values per ``(etf_ticker, source)`` picks each ETF's
  two most recent snapshot dates. Weekends, holidays and missing days are
  handled for free.
* **ISIN is the join key**, falling back to ``constituent_ticker`` where ISIN is
  absent — ``COALESCE(isin, constituent_ticker)``.

Cash sleeves (sentinel ISIN ``CASH``) and identity-less residual rows (FX
currency lines, "other payable & receivables" — no ISIN *and* no ticker) are
excluded from the delta computation: a shared ``CASH`` key cross-multiplies
within an ETF, and a null join key phantoms in/out every run. They remain in the
snapshot store; they are just not deltas.
"""

from __future__ import annotations

import duckdb

# ISO currency codes that show up as bare tickers on FX cash sleeves (BetaShares/
# Amplify list one row per settlement currency). Matched only when the row has no
# ISIN, so real tickers that merely *contain* a code (e.g. ``EUR AU`` = European
# Lithium) are never affected.
_FX_CURRENCY_CODES = (
    "AUD", "CAD", "GBP", "HKD", "JPY", "ZAR", "USD", "EUR", "CHF", "NZD", "SGD",
    "SEK", "NOK", "DKK", "CNY", "CNH", "KRW", "BRL", "MXN", "IDR", "INR", "PLN",
    "TRY", "PHP", "THB", "MYR", "TWD", "SAR", "AED", "ILS", "CLP", "PEN", "COP",
    "HUF", "CZK", "RUB",
)

# Legacy heuristic predicate (on alias ``h``) — applied only to rows written
# before the ``instrument_type`` column existed (NULL there). Excludes: the CASH
# sentinel; identity-less residuals; bare-currency-code FX sleeves; and
# zero-economic rows (weight = 0 AND market_value = 0) — index futures held for
# cash equitization, which report no weight/value and only churn add/remove pairs
# as their quarterly contract rolls. A real holding always has weight or value; a
# tiny name (TATA STEEL, weight rounds to 0.00 but ~$37k value) clears this.
_LEGACY_TRACKABLE = (
    "COALESCE(h.isin, h.constituent_ticker) IS NOT NULL "
    "AND COALESCE(h.isin, h.constituent_ticker) <> 'CASH' "
    "AND NOT (h.isin IS NULL AND UPPER(TRIM(h.constituent_ticker)) IN ("
    + ", ".join(f"'{c}'" for c in _FX_CURRENCY_CODES)
    + ")) "
    "AND NOT (COALESCE(h.weight_pct, 0) = 0 AND COALESCE(h.market_value, 0) = 0)"
)

# Trackable = the classifier tagged the row 'equity' (etf_lite.classify, computed
# at normalise time from the issuer's asset-class label or heuristics). Legacy
# rows with no instrument_type fall back to the heuristic predicate above, so the
# committed history keeps working unchanged.
_TRACKABLE = (
    "(CASE WHEN h.instrument_type IS NOT NULL "
    "THEN h.instrument_type = 'equity' "
    "ELSE (" + _LEGACY_TRACKABLE + ") END)"
)


def daily_deltas_sql(where: str = "") -> str:
    """Return the canonical per-constituent delta SQL.

    ``where`` is injected into the distinct-dates CTE. Pass ``"WHERE source = ?"``
    (optionally with ``" AND as_of_date <= ?"``) for the parametrised query.
    """
    return f"""
    WITH distinct_dates AS (
        SELECT DISTINCT etf_ticker, source, as_of_date
        FROM etf_holdings_snapshot
        {where}
    ),
    ranked_dates AS (
        SELECT etf_ticker, source, as_of_date,
               DENSE_RANK() OVER (
                   PARTITION BY etf_ticker, source
                   ORDER BY as_of_date DESC
               ) AS date_rank
        FROM distinct_dates
    ),
    cur AS (
        SELECT h.*
        FROM etf_holdings_snapshot h
        JOIN ranked_dates d
          ON h.etf_ticker = d.etf_ticker
         AND h.source     = d.source
         AND h.as_of_date = d.as_of_date
        WHERE d.date_rank = 1
          AND {_TRACKABLE}
    ),
    prev AS (
        SELECT h.*
        FROM etf_holdings_snapshot h
        JOIN ranked_dates d
          ON h.etf_ticker = d.etf_ticker
         AND h.source     = d.source
         AND h.as_of_date = d.as_of_date
        WHERE d.date_rank = 2
          AND {_TRACKABLE}
    )
    SELECT
        COALESCE(c.etf_ticker, p.etf_ticker)                 AS etf_ticker,
        COALESCE(c.source, p.source)                         AS source,
        u.commodity_vertical                                 AS commodity_vertical,
        COALESCE(c.constituent_ticker, p.constituent_ticker) AS constituent_ticker,
        COALESCE(c.constituent_name, p.constituent_name)     AS constituent_name,
        COALESCE(c.isin, p.isin)                             AS isin,
        c.as_of_date                                         AS curr_as_of_date,
        p.as_of_date                                         AS prev_as_of_date,
        CASE
            WHEN p.snapshot_id IS NULL THEN 'addition'
            WHEN c.snapshot_id IS NULL THEN 'removal'
            ELSE 'change'
        END                                                  AS delta_type,
        p.weight_pct                                         AS prev_weight_pct,
        c.weight_pct                                         AS curr_weight_pct,
        COALESCE(c.weight_pct, 0) - COALESCE(p.weight_pct, 0) AS delta_weight_pct,
        p.shares_held                                        AS prev_shares,
        c.shares_held                                        AS curr_shares,
        COALESCE(c.shares_held, 0) - COALESCE(p.shares_held, 0) AS delta_shares,
        CASE WHEN p.shares_held > 0
             THEN ((c.shares_held - p.shares_held) / p.shares_held) * 100
             ELSE NULL END                                   AS pct_change_shares,
        p.market_value                                       AS prev_market_value,
        c.market_value                                       AS curr_market_value,
        COALESCE(c.market_value, 0) - COALESCE(p.market_value, 0) AS delta_market_value
    FROM cur c
    FULL OUTER JOIN prev p
      ON c.etf_ticker = p.etf_ticker
     AND c.source     = p.source
     AND COALESCE(c.isin, c.constituent_ticker)
       = COALESCE(p.isin, p.constituent_ticker)
    LEFT JOIN etf_universe u
      ON COALESCE(c.etf_ticker, p.etf_ticker) = u.etf_ticker
    """


def cross_etf_sql(source_relation: str = "v_daily_deltas", min_converging: int = 2) -> str:
    """Return the cross-ETF aggregation SQL, grouping deltas by ISIN."""
    return f"""
    SELECT
        isin,
        ANY_VALUE(constituent_name) AS constituent_name,
        COUNT(DISTINCT etf_ticker) AS n_etfs,
        COUNT(*) FILTER (WHERE delta_weight_pct > 0) AS n_etfs_weight_up,
        COUNT(*) FILTER (WHERE delta_weight_pct < 0) AS n_etfs_weight_down,
        COUNT(*) FILTER (WHERE delta_type = 'addition') AS n_etfs_added,
        COUNT(*) FILTER (WHERE delta_type = 'removal') AS n_etfs_removed,
        SUM(delta_market_value) AS total_delta_market_value,
        LIST(STRUCT_PACK(
            etf_ticker         := etf_ticker,
            delta_type         := delta_type,
            delta_weight_pct   := delta_weight_pct,
            delta_shares       := delta_shares,
            delta_market_value := delta_market_value
        )) AS etf_details
    FROM {source_relation}
    WHERE isin IS NOT NULL AND isin <> 'CASH'
    GROUP BY isin
    HAVING COUNT(DISTINCT etf_ticker) >= {min_converging}
    ORDER BY ABS(COALESCE(SUM(delta_market_value), 0)) DESC
    """


def rows_to_dicts(cur: duckdb.DuckDBPyConnection) -> list[dict]:
    """Materialise the most recent ``cur.execute(...)`` result as a list of dicts."""
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_daily_deltas(
    conn: duckdb.DuckDBPyConnection,
    source: str,
    *,
    as_of: object | None = None,
) -> list[dict]:
    """Run the canonical delta query for one ``source``."""
    where = "WHERE source = ?"
    params: list[object] = [source]
    if as_of is not None:
        where += " AND as_of_date <= ?"
        params.append(as_of)
    return rows_to_dicts(conn.execute(daily_deltas_sql(where), params))
