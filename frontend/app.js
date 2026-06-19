"use strict";

// Minimal static dashboard. Reads ./data.json (written by etf_lite.build) and
// renders three views: Deltas, Cross-ETF consensus, and Coverage.
// All DOM is built with textContent / createElement — no innerHTML.

let DATA = null;
let TAB = "deltas";

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
const signClass = (v) => (v == null || v === 0 ? "dim" : v > 0 ? "pos" : "neg");

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

// -- tables -----------------------------------------------------------------
function table(cols, rows, rowFn) {
  if (!rows.length) return el("div", { class: "empty" }, "none");
  const thead = el("tr", {}, ...cols.map((c) =>
    el("th", { class: c.num ? "num" : "" }, c.label)));
  const tbody = el("tbody", {}, ...rows.map(rowFn));
  return el("table", {}, el("thead", {}, thead), tbody);
}

function td(text, cls = "") { return el("td", { class: cls }, text == null ? "—" : String(text)); }
function tdNum(text, cls = "") { return el("td", { class: ("num " + cls).trim() }, text == null ? "—" : String(text)); }

// -- Deltas view ------------------------------------------------------------
function viewDeltas() {
  const d = DATA, s = d.summary || {};
  const wrap = el("div", {});

  wrap.appendChild(el("div", { class: "chips" },
    chip("add", s.total_additions, "added"),
    chip("rem", s.total_removals, "removed"),
    chip("chg", s.total_significant_changes, "material changes"),
    chip("", s.etfs_processed, "ETFs compared")
  ));

  wrap.appendChild(section("Additions", d.additions, () =>
    table(
      [{ label: "ETF" }, { label: "Ticker" }, { label: "Name" }, { label: "Weight", num: true }, { label: "Value Δ", num: true }],
      [...d.additions].sort((a, b) => (b.curr_weight_pct || 0) - (a.curr_weight_pct || 0)),
      (r) => el("tr", {},
        td(r.etf_ticker, "tk"), td(r.constituent_ticker), td(clip(r.constituent_name), "nm"),
        tdNum(fmtW(r.curr_weight_pct)), tdNum(fmtUsd(r.delta_market_value), "pos"))
    )));

  wrap.appendChild(section("Removals", d.removals, () =>
    table(
      [{ label: "ETF" }, { label: "Ticker" }, { label: "Name" }, { label: "Was", num: true }, { label: "Value Δ", num: true }],
      [...d.removals].sort((a, b) => (b.prev_weight_pct || 0) - (a.prev_weight_pct || 0)),
      (r) => el("tr", {},
        td(r.etf_ticker, "tk"), td(r.constituent_ticker), td(clip(r.constituent_name), "nm"),
        tdNum(fmtW(r.prev_weight_pct)), tdNum(fmtUsd(r.delta_market_value), "neg"))
    )));

  wrap.appendChild(section("Weight / Share Changes", d.changes, () =>
    table(
      [{ label: "ETF" }, { label: "Ticker" }, { label: "Name" }, { label: "Weight Δ", num: true }, { label: "Shares Δ", num: true }, { label: "Value Δ", num: true }],
      [...d.changes].sort((a, b) => Math.abs(b.delta_weight_pct || 0) - Math.abs(a.delta_weight_pct || 0)),
      (r) => el("tr", {},
        td(r.etf_ticker, "tk"), td(r.constituent_ticker), td(clip(r.constituent_name), "nm"),
        tdNum(fmtPct(r.delta_weight_pct), signClass(r.delta_weight_pct)),
        tdNum(r.pct_change_shares == null ? "—" : `${r.pct_change_shares >= 0 ? "+" : ""}${r.pct_change_shares.toFixed(1)}%`, signClass(r.pct_change_shares)),
        tdNum(fmtUsd(r.delta_market_value), signClass(r.delta_market_value)))
    )));

  return wrap;
}

// -- Cross-ETF view ---------------------------------------------------------
function viewCross() {
  const sigs = DATA.cross_etf_signals || [];
  const wrap = el("div", {});
  wrap.appendChild(el("div", { class: "section" },
    el("h2", {}, "Cross-ETF Consensus", el("span", { class: "count" }, String(sigs.length))),
    el("div", { class: "dim", style: "margin-bottom:8px;font-size:11px" },
      "Constituents moved by ≥2 ETFs the same day. Value Δ mixes fund currencies (indicative).")
  ));
  wrap.appendChild(table(
    [{ label: "Name" }, { label: "ISIN" }, { label: "ETFs", num: true }, { label: "↑", num: true }, { label: "↓", num: true }, { label: "+", num: true }, { label: "−", num: true }, { label: "Value Δ", num: true }, { label: "Funds" }],
    sigs,
    (g) => el("tr", {},
      td(clip(g.constituent_name || g.constituent_ticker), "nm"),
      td(g.isin, "dim"),
      tdNum(g.n_etfs, "tk"),
      tdNum(g.n_etfs_weight_up || 0, "pos"),
      tdNum(g.n_etfs_weight_down || 0, "neg"),
      tdNum(g.n_etfs_added || 0, "pos"),
      tdNum(g.n_etfs_removed || 0, "neg"),
      tdNum(fmtUsd(g.total_delta_market_value), signClass(g.total_delta_market_value)),
      td((g.etf_details || []).map((x) => x.etf_ticker).join(", "), "dim"))
  ));
  return wrap;
}

// -- Coverage view ----------------------------------------------------------
function viewCoverage() {
  const etfs = DATA.etfs || [];
  return el("div", {},
    el("div", { class: "section" }, el("h2", {}, "ETF Coverage — today's run")),
    table(
      [{ label: "ETF" }, { label: "Vertical" }, { label: "Status" }, { label: "As-of" }, { label: "Rows", num: true }, { label: "Note" }],
      etfs,
      (e) => el("tr", {},
        td(e.etf_ticker, "tk"),
        td((e.commodity_vertical || "").replace(/_/g, " "), "dim"),
        el("td", {}, el("span", { class: "tag " + e.status }, e.status)),
        td(e.as_of_date || e.latest_stored, "mono"),
        tdNum(e.n_today || 0),
        td(e.error ? clip(e.error, 80) : "", "dim"))
    )
  );
}

// -- helpers ----------------------------------------------------------------
function chip(cls, n, lbl) {
  return el("div", { class: "chip " + cls }, el("b", {}, String(n ?? 0)), el("span", { class: "lbl" }, lbl));
}
function section(title, rows, bodyFn) {
  return el("div", { class: "section" },
    el("h2", {}, title, el("span", { class: "count" }, String((rows || []).length))),
    bodyFn());
}
function clip(s, n = 38) { s = s || ""; return s.length > n ? s.slice(0, n - 1) + "…" : s; }

// -- routing ----------------------------------------------------------------
function render() {
  const view = TAB === "cross" ? viewCross() : TAB === "coverage" ? viewCoverage() : viewDeltas();
  $("#view").replaceChildren(view);
  document.querySelectorAll("#tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === TAB));
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
    `etf-flow-lite · ${DATA.source || "web_csv"} · ${(DATA.etfs || []).length} ETFs · static build, no live server`;
}

init();
