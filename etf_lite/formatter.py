"""Format a DeltaResult into a concise Telegram alert (ported from the full
tracker). Pure formatting — no Telegram dependency, fully testable.

Tabular sections use ``<pre>`` (HTML parse mode) so columns line up in Telegram's
monospace; set ``include_monospace_blocks: false`` for plain text. Empty sections
are omitted; if the message would exceed the limit, Major Moves is trimmed first.
"""

from __future__ import annotations

import html
from datetime import date

DEFAULTS = {
    "max_additions": 10,
    "max_removals": 10,
    "max_major_moves": 10,
    "max_cross_etf_signals": 5,
    "message_char_limit": 4096,
    "include_monospace_blocks": True,
    "dashboard_url": "https://fmarkiv.github.io/Terra-ETF-Lite/",
}


def abbrev_shares(n) -> str:
    if n is None:
        return "—"
    sign = "+" if n >= 0 else "-"
    a = abs(float(n))
    if a >= 1e9:
        return f"{sign}{a / 1e9:.1f}B"
    if a >= 1e6:
        v = a / 1e6
        return f"{sign}{v:.0f}M" if v >= 10 else f"{sign}{v:.1f}M"
    if a >= 1e3:
        return f"{sign}{a / 1e3:.0f}K"
    return f"{sign}{a:.0f}"


def _fmt_date(d) -> str:
    if not isinstance(d, date):
        return str(d) if d else "—"
    return f"{d.day} {d:%b %Y}"


def _label(name, ticker) -> str:
    name = (name or "").strip()
    if ticker:
        return f"{name} ({ticker})"
    return name or (ticker or "—")


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def _pre(lines: list[str], mono: bool) -> str:
    body = "\n".join(lines)
    return f"<pre>\n{_esc(body)}\n</pre>" if mono else _esc(body)


def _more(n_hidden: int) -> str:
    return f"\n… and {n_hidden} more." if n_hidden else ""


def _additions_block(rows, mono, cap=None) -> str | None:
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: -(r.get("curr_weight_pct") or 0))
    total = len(rows)
    shown = rows[:cap] if cap else rows
    etf_w = max(len(r["etf_ticker"]) for r in shown)
    lbls = [f"+{_label(r.get('constituent_name'), r.get('constituent_ticker'))}" for r in shown]
    lbl_w = max(len(s) for s in lbls)
    lines = [
        f"{r['etf_ticker']:<{etf_w}}  {lbl:<{lbl_w}}  {(r.get('curr_weight_pct') or 0):>4.1f}% wt"
        for r, lbl in zip(shown, lbls)
    ]
    return f"🆕 NEW ADDITIONS ({total})\n" + _pre(lines, mono) + _more(total - len(shown))


def _removals_block(rows, mono, cap=None) -> str | None:
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: -(r.get("prev_weight_pct") or 0))
    total = len(rows)
    shown = rows[:cap] if cap else rows
    etf_w = max(len(r["etf_ticker"]) for r in shown)
    lbls = [f"-{_label(r.get('constituent_name'), r.get('constituent_ticker'))}" for r in shown]
    lbl_w = max(len(s) for s in lbls)
    lines = [
        f"{r['etf_ticker']:<{etf_w}}  {lbl:<{lbl_w}}  was {(r.get('prev_weight_pct') or 0):>4.1f}% wt"
        for r, lbl in zip(shown, lbls)
    ]
    return f"🚫 REMOVALS ({total})\n" + _pre(lines, mono) + _more(total - len(shown))


def _major_moves_block(rows, mono, cap) -> tuple[str | None, int]:
    if not rows:
        return None, 0
    ordered = sorted(rows, key=lambda r: -abs(r.get("delta_weight_pct") or 0))
    shown = ordered[:cap] if cap is not None else ordered
    n_hidden = len(ordered) - len(shown)
    if not shown:
        return None, len(ordered)

    etf_w = max(len(r["etf_ticker"]) for r in shown)
    lbls = [_label(r.get("constituent_name"), r.get("constituent_ticker")) for r in shown]
    lbl_w = max(len(s) for s in lbls)
    lines = []
    for r, lbl in zip(shown, lbls):
        wt = f"{(r.get('delta_weight_pct') or 0):+.2f}% wt"
        shr = f"{abbrev_shares(r.get('delta_shares'))} shr"
        lines.append(f"{r['etf_ticker']:<{etf_w}}  {lbl:<{lbl_w}}  {wt:>10}  {shr:>9}")
    block = f"📈 MAJOR MOVES ({len(ordered)})\n" + _pre(lines, mono)
    if n_hidden:
        block += f"\n… and {n_hidden} more. See dashboard for full details."
    return block, n_hidden


def _cross_etf_block(signals, cap) -> str | None:
    if not signals:
        return None
    total = len(signals)
    ordered = sorted(signals, key=lambda s: -s.get("n_etfs", 0))[:cap]
    lines = []
    for s in ordered:
        details = s.get("etf_details", [])
        up = [d["etf_ticker"] for d in details
              if (d.get("delta_weight_pct") or 0) > 0 or d.get("delta_type") == "addition"]
        down = [d["etf_ticker"] for d in details
                if (d.get("delta_weight_pct") or 0) < 0 or d.get("delta_type") == "removal"]
        if len(up) >= len(down):
            arrow, etfs = "↑", up
        else:
            arrow, etfs = "↓", down
        label = _label(s.get("constituent_name"), s.get("constituent_ticker"))
        lines.append(f"{_esc(label)}: wt {arrow} in {_esc(', '.join(etfs))}")
    block = f"🔀 CROSS-ETF SIGNALS ({total})\n" + "\n".join(lines)
    return block + _more(total - len(ordered))


def format_alert(result, config: dict | None = None) -> str:
    """Format ``result`` (a DeltaResult) into the alert string."""
    cfg = {**DEFAULTS, **(config or {})}
    mono = bool(cfg["include_monospace_blocks"])
    limit = int(cfg["message_char_limit"])

    s = result.summary or {}
    counts = []
    if result.additions:
        counts.append(f"🆕 {len(result.additions)} add")
    if result.removals:
        counts.append(f"🚫 {len(result.removals)} rm")
    if result.changes:
        counts.append(f"📈 {len(result.changes)} moves")
    if result.cross_etf_signals:
        counts.append(f"🔀 {len(result.cross_etf_signals)} cross-ETF")
    title = cfg.get("title") or "📊 ETF Holdings Delta"
    header = (
        f"{title} — {_fmt_date(result.as_of_date)}\n"
        f"vs {_fmt_date(result.previous_date)} · "
        f"{s.get('etfs_processed', 0)} ETFs · {result.source}"
    )
    if counts:
        header += "\n" + " · ".join(counts)

    additions = _additions_block(result.additions, mono, cfg["max_additions"])
    removals = _removals_block(result.removals, mono, cfg["max_removals"])
    cross = _cross_etf_block(result.cross_etf_signals, cfg["max_cross_etf_signals"])
    footer = f"🔎 Full detail: {cfg['dashboard_url']}" if cfg.get("dashboard_url") else None

    def assemble(cap):
        moves, _ = _major_moves_block(result.changes, mono, cap)
        parts = [b for b in (header, additions, removals, moves, cross, footer) if b]
        return "\n\n".join(parts), moves

    cap = cfg["max_major_moves"]
    message, moves = assemble(cap)
    while moves and len(message) > limit and cap > 0:
        cap -= 1
        message, moves = assemble(cap)

    if not (additions or removals or moves or cross):
        message += "\n\n✅ No material changes today."
    return message
