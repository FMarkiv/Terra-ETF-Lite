"use strict";

// Minimal static dashboard. Reads ./data.json (written by etf_lite.build) and
// renders three views: Deltas, Cross-ETF consensus, and Coverage.
// All DOM is built with textContent / createElement — no innerHTML.
//
// Each table has clickable, sortable column headers; each tab has its own
// filter box. Sort + filter state is isolated per table / per tab.

let DATA = null;
let TAB = "deltas";
const SORT = {};    // tableId -> { col: <index>, dir: "asc"|"desc" }
const FILTER = {};  // tab -> query string

const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else n.setAttribute(k, v);
  }
  for (const kid of kids) {
    if (kid == null) continue;
    n.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid);
  }
  return n;
}

// -- formatting -------------------------------------------------------------
const fmtPct = (v) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(3)}%`);
const fmtW = (v) => (v == null ? "—" : `${v.toFixed(2)}%`);
function fmtUsd(v) {
  if (v == null) return "—";
  const sign = v >= 0 ? "+" : "−";
  const a = Math.abs(v);
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(1)}k`;
  return `${sign}$${a.toFixed(0)}`;
}
const fmtShares = (v) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`);
const signClass = (v) => (v == null || v === 0 ? "dim" : v > 0 ? "pos" : "neg");

function td(text, cls = "") { return el("td", { class: cls }, text == null ? "—" : String(text)); }
function tdNum(text, cls = "") { return el("td", { class: ("num " + cls).trim() }, text == null ? "—" : String(text)); }

// Column factories: a column has {label, num, sort:(row)->comparable, cell:(row)->node}.
// numcol `absSort` ranks by magnitude (biggest mover, either direction) — used
// for the Δ columns so the default sort surfaces the largest moves first.
function col(label, sort, cell) { return { label, num: false, sort, cell: cell || ((r) => td(sort(r))) }; }
function numcol(label, sort, render, cls, absSort = false) {
  return {
    label, num: true, sort, absSort,
    cell: (r) => tdNum(render(r), typeof cls === "function" ? cls(r) : (cls || "")),
  };
}

// -- generic sortable table -------------------------------------------------
function dataTable(id, columns, rows, defaultSort) {
  if (!rows.length) return el("div", { class: "empty" }, "none");

  // Seed a sensible initial ranking (with a visible arrow) until the user clicks.
  if (!SORT[id] && defaultSort) SORT[id] = { ...defaultSort };

  const st = SORT[id];
  let data = rows.slice();
  if (st) {
    const c = columns[st.col];
    const dir = st.dir === "asc" ? 1 : -1;
    data.sort((a, b) => {
      let va = c.sort(a), vb = c.sort(b);
      if (c.num) {
        va = (va == null ? -Infinity : (c.absSort ? Math.abs(va) : va));
        vb = (vb == null ? -Infinity : (c.absSort ? Math.abs(vb) : vb));
        return (va - vb) * dir;
      }
      va = (va == null ? "" : String(va)).toLowerCase();
      vb = (vb == null ? "" : String(vb)).toLowerCase();
      return va < vb ? -dir : va > vb ? dir : 0;
    });
  }

  const headRow = el("tr", {}, ...columns.map((c, i) => {
    const active = st && st.col === i;
    const arrow = active ? el("span", { class: "arrow" }, st.dir === "asc" ? "▲" : "▼") : null;
    const th = el("th", { class: ((c.num ? "num " : "") + "sortable").trim() }, c.label, arrow);
    th.addEventListener("click", () => {
      if (st && st.col === i) st.dir = st.dir === "asc" ? "desc" : "asc";
      else SORT[id] = { col: i, dir: c.num ? "desc" : "asc" };  // numbers default high→low
      render();
    });
    return th;
  }));

  const body = el("tbody", {}, ...data.map((r) => el("tr", {}, ...columns.map((c) => c.cell(r)))));
  return el("table", {}, el("thead", {}, headRow), body);
}

// -- per-tab filter bar -----------------------------------------------------
function filterBar(tab, total, shown) {
  const inp = el("input", { type: "text", id: "filterInput", placeholder: "filter — ticker, name, ETF…" });
  inp.value = FILTER[tab] || "";
  inp.addEventListener("input", () => { FILTER[tab] = inp.value; render(); });
  const count = el("span", { class: "fcount" },
    FILTER[tab] ? `${shown} of ${total}` : `${total}`);
  return el("div", { class: "filterbar" }, inp, count);
}

function matcher(q, fields) {
  q = (q || "").toLowerCase().trim();
  if (!q) return () => true;
  return (r) => fields.map((f) => String(r[f] ?? "")).join(" ").toLowerCase().includes(q);
}

// -- Deltas view ------------------------------------------------------------
function viewDeltas() {
  const d = DATA, s = d.summary || {};
  const m = matcher(FILTER.deltas, ["etf_ticker", "constituent_ticker", "constituent_name", "isin"]);
  const adds = d.additions.filter(m), rems = d.removals.filter(m), chgs = d.changes.filter(m);
  const total = d.additions.length + d.removals.length + d.changes.length;
  const shown = adds.length + rems.length + chgs.length;

  const wrap = el("div", {});
  wrap.appendChild(filterBar("deltas", total, shown));
  wrap.appendChild(el("div", { class: "chips" },
    chip("add", s.total_additions, "added"),
    chip("rem", s.total_removals, "removed"),
    chip("chg", s.total_significant_changes, "material changes"),
    chip("", s.etfs_processed, "ETFs compared")
  ));

  // Default sort: additions/removals by weight (largest first); changes by the
  // biggest weight move (magnitude, either direction) — the "what moved most" view.
  wrap.appendChild(deltaSection("Additions", adds, "deltas:add", [
    col("ETF", (r) => r.etf_ticker, (r) => td(r.etf_ticker, "tk")),
    col("Ticker", (r) => r.constituent_ticker),
    col("Name", (r) => r.constituent_name, (r) => td(clip(r.constituent_name), "nm")),
    numcol("Weight", (r) => r.curr_weight_pct, (r) => fmtW(r.curr_weight_pct)),
    numcol("Value Δ", (r) => r.delta_market_value, (r) => fmtUsd(r.delta_market_value), "pos", true),
  ], { col: 3, dir: "desc" }));

  wrap.appendChild(deltaSection("Removals", rems, "deltas:rem", [
    col("ETF", (r) => r.etf_ticker, (r) => td(r.etf_ticker, "tk")),
    col("Ticker", (r) => r.constituent_ticker),
    col("Name", (r) => r.constituent_name, (r) => td(clip(r.constituent_name), "nm")),
    numcol("Was", (r) => r.prev_weight_pct, (r) => fmtW(r.prev_weight_pct)),
    numcol("Value Δ", (r) => r.delta_market_value, (r) => fmtUsd(r.delta_market_value), "neg", true),
  ], { col: 3, dir: "desc" }));

  wrap.appendChild(deltaSection("Weight / Share Changes", chgs, "deltas:chg", [
    col("ETF", (r) => r.etf_ticker, (r) => td(r.etf_ticker, "tk")),
    col("Ticker", (r) => r.constituent_ticker),
    col("Name", (r) => r.constituent_name, (r) => td(clip(r.constituent_name), "nm")),
    numcol("Weight Δ", (r) => r.delta_weight_pct, (r) => fmtPct(r.delta_weight_pct), (r) => signClass(r.delta_weight_pct), true),
    numcol("Shares Δ", (r) => r.pct_change_shares, (r) => fmtShares(r.pct_change_shares), (r) => signClass(r.pct_change_shares), true),
    numcol("Value Δ", (r) => r.delta_market_value, (r) => fmtUsd(r.delta_market_value), (r) => signClass(r.delta_market_value), true),
  ], { col: 3, dir: "desc" }));

  return wrap;
}

// -- Cross-ETF view ---------------------------------------------------------
function viewCross() {
  const all = DATA.cross_etf_signals || [];
  const m = matcher(FILTER.cross, ["constituent_name", "constituent_ticker", "isin"]);
  const sigs = all.filter(m);
  const fundsStr = (g) => (g.etf_details || []).map((x) => x.etf_ticker).join(", ");

  const wrap = el("div", {});
  wrap.appendChild(filterBar("cross", all.length, sigs.length));
  wrap.appendChild(el("div", { class: "section" },
    el("h2", {}, "Cross-ETF Consensus", el("span", { class: "count" }, String(sigs.length))),
    el("div", { class: "dim", style: "margin-bottom:8px;font-size:11px" },
      "Constituents moved by ≥2 ETFs the same day. Value Δ mixes fund currencies (indicative).")
  ));
  wrap.appendChild(dataTable("cross", [
    col("Name", (g) => g.constituent_name || g.constituent_ticker, (g) => td(clip(g.constituent_name || g.constituent_ticker), "nm")),
    col("ISIN", (g) => g.isin, (g) => td(g.isin, "dim")),
    numcol("ETFs", (g) => g.n_etfs, (g) => g.n_etfs, "tk"),
    numcol("↑", (g) => g.n_etfs_weight_up || 0, (g) => g.n_etfs_weight_up || 0, "pos"),
    numcol("↓", (g) => g.n_etfs_weight_down || 0, (g) => g.n_etfs_weight_down || 0, "neg"),
    numcol("+", (g) => g.n_etfs_added || 0, (g) => g.n_etfs_added || 0, "pos"),
    numcol("−", (g) => g.n_etfs_removed || 0, (g) => g.n_etfs_removed || 0, "neg"),
    numcol("Value Δ", (g) => g.total_delta_market_value, (g) => fmtUsd(g.total_delta_market_value), (g) => signClass(g.total_delta_market_value), true),
    col("Funds", fundsStr, (g) => td(fundsStr(g), "dim")),
  ], sigs, { col: 2, dir: "desc" }));   // default: most funds converging first
  return wrap;
}

// -- Coverage view ----------------------------------------------------------
function viewCoverage() {
  const all = DATA.etfs || [];
  const m = matcher(FILTER.coverage, ["etf_ticker", "commodity_vertical", "status"]);
  const etfs = all.filter(m);

  const wrap = el("div", {});
  wrap.appendChild(filterBar("coverage", all.length, etfs.length));
  wrap.appendChild(el("div", { class: "section" }, el("h2", {}, "ETF Coverage — today's run")));
  wrap.appendChild(dataTable("coverage", [
    col("ETF", (e) => e.etf_ticker, (e) => td(e.etf_ticker, "tk")),
    col("Vertical", (e) => e.commodity_vertical, (e) => td((e.commodity_vertical || "").replace(/_/g, " "), "dim")),
    col("Status", (e) => e.status, (e) => el("td", {}, el("span", { class: "tag " + e.status }, e.status))),
    col("As-of", (e) => e.as_of_date || e.latest_stored, (e) => td(e.as_of_date || e.latest_stored, "mono")),
    numcol("Rows", (e) => e.n_today || 0, (e) => e.n_today || 0),
    col("Note", (e) => e.error, (e) => td(e.error ? clip(e.error, 80) : "", "dim")),
  ], etfs, { col: 0, dir: "asc" }));   // default: alphabetical by ETF
  return wrap;
}

// -- helpers ----------------------------------------------------------------
function chip(cls, n, lbl) {
  return el("div", { class: "chip " + cls }, el("b", {}, String(n ?? 0)), el("span", { class: "lbl" }, lbl));
}
function deltaSection(title, rows, tableId, columns, defaultSort) {
  return el("div", { class: "section" },
    el("h2", {}, title, el("span", { class: "count" }, String(rows.length))),
    dataTable(tableId, columns, rows, defaultSort));
}
function clip(s, n = 38) { s = s || ""; return s.length > n ? s.slice(0, n - 1) + "…" : s; }

// -- header / meta ----------------------------------------------------------
function renderMeta() {
  const d = DATA;
  const cov = d.coverage || {};
  const failTxt = cov.failed
    ? el("span", { class: "warn" }, ` · ${cov.failed} unavailable`)
    : null;
  $("#meta").replaceChildren(
    el("div", {}, el("b", {}, d.previous_date || "—"), " → ", el("b", {}, d.as_of_date || "—")),
    el("div", {},
      `${cov.tracked || 0} tracked · ${(cov.ingested || 0) + (cov.skipped || 0)} current`,
      failTxt),
    el("div", { class: "dim" }, `built ${(d.generated_at || "").replace("T", " ").replace("+00:00", "Z")}`)
  );
}

// -- routing ----------------------------------------------------------------
function render() {
  // Preserve filter-box focus + caret across the re-render.
  const active = document.activeElement;
  const wasFilter = active && active.id === "filterInput";
  const caret = wasFilter ? active.selectionStart : null;

  const view = TAB === "cross" ? viewCross() : TAB === "coverage" ? viewCoverage() : viewDeltas();
  $("#view").replaceChildren(view);
  document.querySelectorAll("#tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === TAB));

  if (wasFilter) {
    const ni = $("#filterInput");
    if (ni) { ni.focus(); if (caret != null) { try { ni.setSelectionRange(caret, caret); } catch (e) {} } }
  }
}

async function init() {
  document.querySelectorAll("#tabs button").forEach((b) =>
    b.addEventListener("click", () => { TAB = b.dataset.tab; render(); }));
  try {
    const resp = await fetch("data.json", { cache: "no-store" });
    DATA = await resp.json();
  } catch (e) {
    $("#view").replaceChildren(el("div", { class: "empty" }, "Could not load data.json — run the build."));
    return;
  }
  renderMeta();
  render();
  $("#foot").textContent =
    `etf-flow-lite · ${DATA.source || "web_csv"} · ${(DATA.etfs || []).length} ETFs · click a header to sort · static build`;
}

init();
