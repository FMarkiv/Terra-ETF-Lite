"""Fill missing ISINs via a one-off Bloomberg sweep.

Some issuers (Global X US, BetaShares, Sprott, parts of Amplify/SPDR) publish
holdings without ISINs, and the cross-fund crosswalk can only borrow an ISIN
when another tracked fund holds the same name *with* one. This round-trips the
remainder through Bloomberg:

  1. export  -> isin_sweep.xlsx: every ISIN-less equity across ALL stored
                history, with BDP formulas (by Bloomberg ticker and by SEDOL).
  2. Open it on the Bloomberg desktop, let it calculate, review, SAVE.
  3. import  -> merges the results into config/isin_overrides.yaml.

The overrides are applied both at ingest (new rows) and when snapshots are
loaded (history), so delta keys stay consistent across days.

    python -m scripts.isin_sweep export [--out isin_sweep.xlsx]
    python -m scripts.isin_sweep import isin_sweep.xlsx
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yaml
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from etf_lite.isin_resolver import DEFAULT_OVERRIDES_PATH, IsinResolver, load_overrides
from etf_lite.store import SNAPSHOT_DIR

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_XLSX = REPO_ROOT / "isin_sweep.xlsx"
SHEET = "Missing ISINs"
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
# Bloomberg exchange codes are 2 chars ("CN", "AU", "C2", "LN", "UN", ...).
_HAS_EXCH = re.compile(r"^\S+ [A-Z0-9]{2}$")

HEADERS = [
    "source_ticker", "name", "etfs", "sedol", "country", "last_seen",
    "bbg_security", "isin_by_ticker", "isin_by_sedol", "bbg_name",
    "isin_final", "check", "manual_isin",
]


# --------------------------------------------------------------------------- #
# ISIN validation (ISO 6166 check digit)
# --------------------------------------------------------------------------- #
def valid_isin(s: str | None) -> bool:
    if not s or not _ISIN_RE.match(s):
        return False
    digits = "".join(str(int(c, 36)) for c in s[:-1])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return (10 - total % 10) % 10 == int(s[-1])


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #
def missing_names() -> pd.DataFrame:
    """Distinct equity (ticker, name) pairs that still have no ISIN after overrides."""
    frames = [pd.read_csv(p, dtype=str) for p in sorted(SNAPSHOT_DIR.glob("*.csv"))]
    d = pd.concat(frames, ignore_index=True).astype(object)
    d = d.where(pd.notna(d), None)
    d = d[d["isin"].isna() & (d["instrument_type"].fillna("equity") == "equity")]
    d = d[d["constituent_ticker"].notna() | d["constituent_name"].notna()]

    resolver = IsinResolver([], load_overrides())  # overrides only
    covered = d.apply(lambda r: resolver.resolve(r["constituent_ticker"], r["constituent_name"],
                                                 r["country"])[0] is not None, axis=1)
    d = d[~covered]

    g = (d.groupby(["constituent_ticker", "constituent_name"], dropna=False)
           .agg(etfs=("etf_ticker", lambda s: ",".join(sorted(set(s)))),
                sedol=("sedol", lambda s: next((x for x in s if isinstance(x, str)), None)),
                country=("country", lambda s: next((x for x in s if isinstance(x, str)), None)),
                last_seen=("as_of_date", "max"),
                max_wt=("weight_pct", lambda s: pd.to_numeric(s, errors="coerce").max()))
           .reset_index()
           .sort_values(["max_wt"], ascending=False))
    return g


def bbg_security(ticker: str | None) -> str | None:
    if not ticker or not isinstance(ticker, str):
        return None
    t = ticker.strip().upper()
    return f"{t} Equity" if _HAS_EXCH.match(t) else f"{t} US Equity"


def export(out: Path) -> None:
    g = missing_names()
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append(HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for i, r in enumerate(g.itertuples(index=False), start=2):
        tk = r.constituent_ticker if isinstance(r.constituent_ticker, str) else None
        sec = bbg_security(tk)
        ws.append([
            tk, r.constituent_name, r.etfs, r.sedol, r.country, r.last_seen, sec,
            f'=IF(G{i}="","",BDP(G{i},"ID_ISIN"))',
            f'=IF(D{i}="","",BDP("/sedol1/"&D{i},"ID_ISIN"))',
            f'=IF(G{i}="","",BDP(G{i},"NAME"))',
            f'=IF(M{i}<>"",M{i},IF(LEN(H{i})=12,H{i},IF(LEN(I{i})=12,I{i},"")))',
            f'=IF(M{i}<>"","MANUAL",IF(AND(LEN(H{i})=12,LEN(I{i})=12),'
            f'IF(H{i}=I{i},"OK","MISMATCH"),IF(K{i}="","MISSING","ONE SOURCE")))',
            None,
        ])
    widths = [14, 36, 16, 10, 10, 11, 20, 15, 15, 32, 15, 12, 15]
    for j, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(j)].width = w
    for row in ws.iter_rows(min_row=2, min_col=13, max_col=13):
        row[0].fill = PatternFill("solid", fgColor="FFF2CC")
    ws.freeze_panes = "A2"

    info = wb.create_sheet("How to use")
    for line in [
        f"ISIN sweep generated {date.today().isoformat()} — {len(g)} names without an ISIN.",
        "",
        "1. Open on the Bloomberg desktop with the Excel add-in loaded; wait for BDP to finish.",
        "2. Column L flags each row: OK (ticker & SEDOL agree), ONE SOURCE, MISMATCH, MISSING.",
        "3. Review MISMATCH / MISSING. Fix column G (bbg_security) or type an ISIN into",
        "   column M (manual_isin, yellow) — manual wins.",
        "4. SAVE the workbook in Excel (the import reads the calculated values Excel stores).",
        "5. Back on the dev machine:  python -m scripts.isin_sweep import isin_sweep.xlsx",
        "",
        "Rows left blank in column K are skipped. Every ISIN is check-digit validated on import.",
    ]:
        info.append([line])
    info.column_dimensions["A"].width = 100
    wb.save(out)
    print(f"wrote {out} ({len(g)} names)")


# --------------------------------------------------------------------------- #
# import
# --------------------------------------------------------------------------- #
def import_(path: Path) -> int:
    wb = load_workbook(path, data_only=True)
    ws = wb[SHEET]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    col = {h: i for i, h in enumerate(HEADERS)}
    if rows and all(r[col["isin_final"]] is None for r in rows) and any(r[col["bbg_security"]] for r in rows):
        print("No calculated values found — open the file on Bloomberg, let BDP finish, and SAVE it first.")
        return 1

    ov = load_overrides() or {}
    by_ticker = dict(ov.get("by_ticker") or {})
    by_name = dict(ov.get("by_name") or {})
    added, conflicts, invalid, blank, mismatched = 0, [], [], 0, 0

    for r in rows:
        tk, name = r[col["source_ticker"]], r[col["name"]]
        isin = (str(r[col["isin_final"]] or "").strip().upper()) or None
        if r[col["check"]] == "MISMATCH":
            mismatched += 1
            continue  # needs a human decision via manual_isin
        if not isin:
            blank += 1
            continue
        if not valid_isin(isin):
            invalid.append((tk or name, isin))
            continue
        table, key = (by_ticker, tk) if tk else (by_name, name)
        if key in table and table[key] != isin:
            conflicts.append((key, table[key], isin))
            continue
        if key not in table:
            table[key] = isin
            added += 1

    header = [ln for ln in DEFAULT_OVERRIDES_PATH.read_text(encoding="utf-8").splitlines()
              if ln.startswith("#")] if DEFAULT_OVERRIDES_PATH.exists() else []
    body = yaml.safe_dump({"by_ticker": dict(sorted(by_ticker.items())),
                           "by_name": dict(sorted(by_name.items()))},
                          sort_keys=False, allow_unicode=True, width=200)
    DEFAULT_OVERRIDES_PATH.write_text("\n".join(header) + "\n\n" + body, encoding="utf-8")

    print(f"added {added} overrides -> {DEFAULT_OVERRIDES_PATH.relative_to(REPO_ROOT)}")
    print(f"skipped: {blank} blank, {mismatched} ticker/SEDOL mismatch (set manual_isin), "
          f"{len(invalid)} invalid, {len(conflicts)} conflicting with existing")
    for k, v in invalid[:20]:
        print(f"  invalid  {k}: {v}")
    for k, old, new in conflicts[:20]:
        print(f"  conflict {k}: existing {old} vs sweep {new} (kept existing)")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--out", type=Path, default=DEFAULT_XLSX)
    i = sub.add_parser("import")
    i.add_argument("xlsx", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "export":
        export(a.out)
        return 0
    return import_(a.xlsx)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
