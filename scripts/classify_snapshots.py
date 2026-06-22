"""One-off migration: stamp ``instrument_type`` onto existing snapshot CSVs.

Snapshots written before the classifier (etf_lite.classify) have no
``instrument_type`` column, so the delta SQL falls back to a weak heuristic —
which lets real-ISIN cash sleeves through (e.g. PICK's ``XTSLA`` =
"BLK CSH FND TREASURY", a money-market holding with a genuine ISIN and weight).

This runs the classifier over every row and writes the column. Two notes:

* Legacy iShares rows carry the asset-class label ("Cash and/or Derivatives") in
  the ``sector`` column (that's where the old parser mapped it), so we feed
  ``sector`` as the asset-class hint when the dedicated field is empty — which is
  exactly what catches XTSLA. Real GICS sectors ("Materials", "Gold", …) don't
  match any asset-class keyword, so equities stay equities.
* Idempotent: safe to re-run. Numeric fields are read as strings and written
  back unchanged; only ``asset_class``/``instrument_type`` columns are added.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_lite.classify import classify_instrument  # noqa: E402
from etf_lite.store import COLUMNS, SNAPSHOT_DIR  # noqa: E402


def _to_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def migrate_file(path: Path) -> dict:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    counts: dict[str, int] = {}
    types = []
    for row in df.to_dict("records"):
        hint = dict(row)
        hint["weight_pct"] = _to_float(row.get("weight_pct"))
        hint["market_value"] = _to_float(row.get("market_value"))
        # Legacy rows: the asset-class label often sits in `sector`.
        hint["asset_class"] = row.get("asset_class") or row.get("sector")
        t = classify_instrument(hint)
        types.append(t)
        counts[t] = counts.get(t, 0) + 1

    df["instrument_type"] = types
    if "asset_class" not in df.columns:
        df["asset_class"] = ""
    # Write in the canonical column order (creating any missing columns).
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df[COLUMNS].to_csv(path, index=False)
    return counts


def main(argv) -> int:
    files = sorted(SNAPSHOT_DIR.glob("*.csv"))
    if not files:
        print("No snapshot CSVs found.")
        return 1
    grand: dict[str, int] = {}
    for f in files:
        counts = migrate_file(f)
        non_equity = {k: v for k, v in counts.items() if k != "equity"}
        note = f"  ({non_equity})" if non_equity else ""
        print(f"  {f.name:<16} {sum(counts.values()):>5} rows{note}")
        for k, v in counts.items():
            grand[k] = grand.get(k, 0) + v
    print("Totals by instrument_type:", grand)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
