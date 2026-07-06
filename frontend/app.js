"use strict";

// Minimal static dashboard. Reads ./data.json (written by etf_lite.build) and
// renders three views: Deltas, Cross-ETF consensus, and Coverage.
// All DOM is built with textContent / createElement — no innerHTML.
//
// Each table has clickable, sortable column headers; each tab has its own
// filter box. Sort + filter state is isolated per table / per tab.

let DATA = null;
let TAB = "deltas";
let VIEW = "aligned";  // currentView: "aligned" (same-date cross-section) | "latest"
const SORT = {};    // tableId -> { col: <index>, dir: "asc"|"desc" }
const FILTER = {};  // tab -> query string

const $ = (sel) => document.querySelector(sel);

// The delta payload for the selected view. Funds post on different lags, so
// "aligned" pins every fund to a common reference date for a clean same-day
// cross-section; "latest" diffs each fund's own two freshest snapshots (mixed
// windows). Falls back to the top-level fields if `views` is absent.
function activeView() {
  return (DATA.views && DATA.views[VIEW]) ? DATA.views[VIEW] : DATA;
}

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
  const d = activeView(), s = d.summary || {};
  const m = matcher(FILTER.deltas, ["etf_ticker", "constituent_ticker", "constituent_name", "isin"]);
  const adds = d.additions.filter(m), rems = d.removals.filter(m), chgs = d.changes.filter(m);
  const total = d.additions.length + d.removals.length + d.changes.length;
  const shown = adds.length + rems.length + chgs.length;

  // The engine diffs each fund's two latest snapshots, whatever their dates —
  // a fund that hasn't published since before this window keeps re-showing its
  // last diff. Badge those rows with the fund's actual as-of date.
  const stale = staleFundDates();
  const etfCell = (r) => (stale[r.etf_ticker]
    ? el("td", { class: "tk" }, r.etf_ticker, el("span", { class: "dim stale" }, ` ${stale[r.etf_ticker]}`))
    : td(r.etf_ticker, "tk"));
  const staleShown = [...new Set([...adds, ...rems, ...chgs]
    .map((r) => r.etf_ticker).filter((t) => stale[t]))];

  const wrap = el("div", {});
  wrap.appendChild(filterBar("deltas", total, shown));
  wrap.appendChild(el("div", { class: "chips" },
    chip("add", s.total_additions, "added"),
    chip("rem", s.total_removals, "removed"),
    chip("chg", s.total_significant_changes, "material changes"),
    chip("", s.etfs_processed, "ETFs compared")
  ));
  if (staleShown.length) {
    wrap.appendChild(el("div", { class: "stalenote" },
      "⚠ ", staleShown.map((t) => `${t} (last ${stale[t]})`).join(", "),
      " — no new data this window; rows below dated in amber repeat the fund's most recent diff, not today's."));
  }

  // Default sort: additions/removals by weight (largest first); changes by the
  // biggest weight move (magnitude, either direction) — the "what moved most" view.
  wrap.appendChild(deltaSection("Additions", adds, "deltas:add", [
    col("ETF", (r) => r.etf_ticker, etfCell),
    col("Ticker", (r) => r.constituent_ticker),
    col("Name", (r) => r.constituent_name, (r) => td(clip(r.constituent_name), "nm")),
    numcol("Weight", (r) => r.curr_weight_pct, (r) => fmtW(r.curr_weight_pct)),
    numcol("Value Δ", (r) => r.delta_market_value, (r) => fmtUsd(r.delta_market_value), "pos", true),
  ], { col: 3, dir: "desc" }));

  wrap.appendChild(deltaSection("Removals", rems, "deltas:rem", [
    col("ETF", (r) => r.etf_ticker, etfCell),
    col("Ticker", (r) => r.constituent_ticker),
    col("Name", (r) => r.constituent_name, (r) => td(clip(r.constituent_name), "nm")),
    numcol("Was", (r) => r.prev_weight_pct, (r) => fmtW(r.prev_weight_pct)),
    numcol("Value Δ", (r) => r.delta_market_value, (r) => fmtUsd(r.delta_market_value), "neg", true),
  ], { col: 3, dir: "desc" }));

  wrap.appendChild(deltaSection("Weight / Share Changes", chgs, "deltas:chg", [
    col("ETF", (r) => r.etf_ticker, etfCell),
    col("Ticker", (r) => r.constituent_ticker),
    col("Name", (r) => r.constituent_name, (r) => td(clip(r.constituent_name), "nm")),
    numcol("Weight Δ", (r) => r.delta_weight_pct, (r) => fmtPct(r.delta_weight_pct), (r) => signClass(r.delta_weight_pct), true),
    numcol("Shares Δ", (r) => r.pct_change_shares, (r) => fmtShares(r.pct_change_shares), (r) => signClass(r.pct_change_shares), true),
    numcol("Value Δ", (r) => r.delta_market_value, (r) => fmtUsd(r.delta_market_value), (r) => signClass(r.delta_market_value), true),
  ], { col: 3, dir: "desc" }));

  return wrap;
}

// -- Cross-ETF view ---------------------------------------------------------
// Funds whose latest snapshot predates the current compare window (e.g. the
// desktop-fed Sprott funds between scrapes) keep re-emitting their last diff.
function staleFundDates() {
  const v = activeView();
  const out = {};
  for (const e of DATA.etfs || []) {
    const d = e.as_of_date || e.latest_stored;
    if (d && v.previous_date && d < v.previous_date) out[e.etf_ticker] = d;
  }
  return out;
}

function viewCross() {
  const v = activeView();
  const all = v.cross_etf_signals || [];
  const m = matcher(FILTER.cross, ["constituent_name", "constituent_ticker", "isin"]);
  const sigs = all.filter(m);

  const stale = staleFundDates();
  const fundsStr = (g) => (g.etf_details || [])
    .map((x) => (stale[x.etf_ticker] ? x.etf_ticker + "*" : x.etf_ticker)).join(", ");
  const changed = (g) => (g.etf_details || []).filter((x) => x.delta_type === "change");
  const unitsUp = (g) => changed(g).filter((x) => (x.delta_shares || 0) > 0).length;
  const unitsDown = (g) => changed(g).filter((x) => (x.delta_shares || 0) < 0).length;
  const anyStale = sigs.some((g) => (g.etf_details || []).some((x) => stale[x.etf_ticker]));

  const wrap = el("div", {});
  wrap.appendChild(filterBar("cross", all.length, sigs.length));
  wrap.appendChild(el("div", { class: "section" },
    el("h2", {}, "Cross-ETF Consensus", el("span", { class: "count" }, String(sigs.length)),
      el("span", { class: "sub" }, `${v.previous_date || "—"} → ${v.as_of_date || "—"}`)),
    el("div", { class: "dim", style: "margin-bottom:8px;font-size:11px" },
      "Constituents moved by ≥2 ETFs. Weight ↑/↓ moves with price as much as trading — ",
      "Units ↑/↓ (share-count direction) is the actual buy/sell signal. ",
      "Value Δ mixes fund currencies (indicative).",
      anyStale ? " * = stale fund: snapshot predates this window, its moves are older." : "")
  ));
  wrap.appendChild(dataTable("cross", [
    col("Name", (g) => g.constituent_name || g.constituent_ticker, (g) => td(clip(g.constituent_name || g.constituent_ticker), "nm")),
    col("ISIN", (g) => g.isin, (g) => td(g.isin, "dim")),
    numcol("ETFs", (g) => g.n_etfs, (g) => g.n_etfs, "tk"),
    numcol("Units ↑", unitsUp, (g) => unitsUp(g) || "·", (g) => (unitsUp(g) ? "pos" : "dim")),
    numcol("Units ↓", unitsDown, (g) => unitsDown(g) || "·", (g) => (unitsDown(g) ? "neg" : "dim")),
    numcol("Added", (g) => g.n_etfs_added || 0, (g) => g.n_etfs_added || "·", (g) => (g.n_etfs_added ? "pos" : "dim")),
    numcol("Removed", (g) => g.n_etfs_removed || 0, (g) => g.n_etfs_removed || "·", (g) => (g.n_etfs_removed ? "neg" : "dim")),
    numcol("Wt ↑", (g) => g.n_etfs_weight_up || 0, (g) => g.n_etfs_weight_up || "·", "dim"),
    numcol("Wt ↓", (g) => g.n_etfs_weight_down || 0, (g) => g.n_etfs_weight_down || "·", "dim"),
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

// -- Guide view ---------------------------------------------------------------
// Static explainer: what each tab/table/column means and how often data moves.
// Keep the threshold numbers in sync with config/thresholds.yaml and the
// schedule with .github/workflows/daily.yml.
function guideSection(title, ...kids) {
  return el("div", { class: "section guide" }, el("h2", {}, title), ...kids);
}
function p(...kids) { return el("p", { class: "gp" }, ...kids); }
function defTable(rows) {
  return el("table", { class: "gdef" }, el("tbody", {},
    ...rows.map(([term, desc]) => el("tr", {},
      el("td", { class: "gterm" }, term), el("td", { class: "gdesc" }, desc)))));
}
function b(s) { return el("b", {}, s); }

function viewGuide() {
  const wrap = el("div", { class: "guidewrap" });

  wrap.appendChild(guideSection("What this is",
    p("A daily snapshot-diff of mining & resources ETF holdings. Each run downloads every ",
      "tracked fund's published holdings file, stores it as a snapshot, and diffs it against ",
      "the previous stored snapshot. Everything on this page describes the change between ",
      "those two dates — shown in the header as ", b("previous → current"), ".")));

  wrap.appendChild(guideSection("Update frequency",
    defTable([
      ["Scheduled build", "GitHub Actions runs Tue–Sat 07:00 Sydney time (Mon–Fri 21:00 UTC) — one run per US trading day, after issuers have posted the previous US close and ASX files are in. No runs Sun/Mon Sydney (US markets closed)."],
      ["Auto-scraped funds", "17 funds (VanEck, Global X, iShares, SPDR, Betashares, Amplify) are fetched from issuer web CSVs on every scheduled run."],
      ["Sprott funds", "SETM and URNM are marked “external” — their holdings come from a separate desktop browser scraper, so they update when that scraper is run, not on the CI schedule."],
      ["No new file", "If an issuer hasn't published a fresh file (same as-of date as already stored), the fund shows as “skipped” in Coverage and contributes no deltas that day."],
      ["“built” timestamp", "The header's built time (UTC) is when the site was last generated — the freshest possible data age."],
    ])));

  wrap.appendChild(guideSection("Deltas tab",
    p("Three tables comparing the current snapshot to the previous one, per fund. ",
      "The summary chips count rows across all funds: added / removed / material changes, ",
      "and how many ETFs had both snapshots available to compare."),
    defTable([
      ["Additions", "Holdings present today that were absent from the previous snapshot. “Weight” is the position's current portfolio weight; “Value Δ” is the market value of the new position. Additions always surface — no threshold."],
      ["Removals", "Holdings in the previous snapshot that are gone today. “Was” is the weight it held before removal. Also always surfaced."],
      ["Weight / Share Changes", "Existing positions whose move was material. A change surfaces if ANY of these trip: |weight Δ| ≥ 0.25% (gold funds 0.30%, copper 0.20%), |shares Δ| ≥ 5%, or |value Δ| ≥ $1M."],
      ["Weight Δ", "Change in portfolio weight, in percentage points (e.g. +0.310% means the position went from say 2.1% to 2.41% of the fund)."],
      ["Shares Δ", "Percent change in the number of shares/units the fund holds — the cleanest buy/sell signal, immune to price moves."],
      ["Value Δ", "Change in the position's market value in the fund's reporting currency. Note a position's value can rise on price alone with zero shares bought."],
      ["Default sort", "Numeric columns rank by magnitude — biggest mover first, in either direction. Click any header to re-sort."],
      ["Stale funds", "Deltas compare each fund's two most recent snapshots, whatever their dates. A fund that hasn't published since before the header's window (e.g. Sprott between desktop scrapes) re-shows its last diff every day — those rows carry an amber date next to the ETF ticker, and a warning banner lists them."],
    ])));

  wrap.appendChild(guideSection("Cross-ETF tab",
    p("One row per underlying company that moved in ≥ 2 funds on the same day — ",
      "several managers acting on the same name at once is a stronger signal than one. ",
      "Cross-listings of the same fund (e.g. GDX and its ASX listing) count as a single vote."),
    defTable([
      ["ETFs", "How many distinct funds moved this constituent today."],
      ["Units ↑ / Units ↓", "Funds whose SHARE COUNT in the name rose vs fell — actual buying/selling. This is the signal to trust."],
      ["Added / Removed", "Funds that newly added vs entirely removed the position — the strongest signal of all."],
      ["Wt ↑ / Wt ↓", "Funds whose portfolio WEIGHT rose vs fell. Weight = price × shares ÷ fund size, so a sector-wide price move pushes every holder's weight the same way with zero trading — broad Wt agreement with no Units moves is price drift, not accumulation."],
      ["Value Δ", "Sum of the value changes across those funds. Indicative only — it mixes fund reporting currencies (USD/AUD) without conversion, and rises on price alone."],
      ["Funds", "Which ETF tickers moved it. A * marks a stale fund whose snapshot predates the compare window — its “move” is older than the others."],
    ])));

  wrap.appendChild(guideSection("Coverage tab",
    p("Per-fund health report for today's run — use it to judge how complete the Deltas are. ",
      "“As-of” is the holdings date inside the issuer's file (not the download time); ",
      "“Rows” is the number of holdings ingested."),
    defTable([
      ["ingested", "New snapshot fetched and stored — this fund's deltas are current."],
      ["skipped", "Issuer file unchanged since last run (same as-of date). Not an error — there's just nothing new yet."],
      ["external", "Sprott fund fed by the desktop scraper — updated outside the CI schedule."],
      ["failed / no_data", "Download or parse failed, or the file had no usable holdings. This fund's deltas are missing today; the header shows a count of unavailable funds."],
      ["future_date", "Issuer published a file dated in the future (their timezone quirk) — held back until the date is valid."],
    ])));

  wrap.appendChild(guideSection("Reading notes",
    defTable([
      ["Weights", "As reported by each issuer — not recomputed. Small funds' weights move on flows; big funds' weights also drift on price."],
      ["Currencies", "Value Δ is in each fund's own reporting currency; cross-ETF totals mix them."],
      ["Filter boxes", "Each tab's filter matches ticker, company name, ISIN or fund — state is kept per tab."],
      ["Alerts", "A Telegram alert with the day's material moves goes out after each scheduled build (auto-scraped funds only)."],
    ])));

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
  const d = DATA, v = activeView();
  const cov = d.coverage || {};
  const failTxt = cov.failed
    ? el("span", { class: "warn" }, ` · ${cov.failed} unavailable`)
    : null;
  // As-of line reflects the selected view: Aligned pins every fund to one date;
  // Latest shows each fund's own freshest window (dates mix across funds).
  const asOfLine = VIEW === "latest"
    ? el("div", {}, "Latest per fund — ",
        el("b", {}, v.as_of_date || "—"), " vs ", el("b", {}, v.previous_date || "—"),
        el("span", { class: "dim" }, " (mixed windows)"))
    : el("div", {}, "Aligned cross-section — all funds as of ",
        el("b", {}, d.aligned_date || v.as_of_date || "—"));
  $("#meta").replaceChildren(
    asOfLine,
    el("div", {},
      `${cov.tracked || 0} tracked · ${(cov.ingested || 0) + (cov.skipped || 0)} current`,
      failTxt),
    el("div", { class: "dim" }, `built ${(d.generated_at || "").replace("T", " ").replace("+00:00", "Z")}`)
  );
}

// -- view toggle (aligned vs latest) ----------------------------------------
const VIEW_HELP = {
  aligned: "Aligned = every fund compared on the same date (accurate cross-section, may lag to the slowest fund).",
  latest: "Latest = each fund's freshest data (mixed dates).",
};
function updateViewHelp() {
  const h = $("#viewhelp");
  if (h) h.textContent = VIEW_HELP[VIEW] || "";
}
function setView(v) {
  if (v !== "aligned" && v !== "latest") return;
  VIEW = v;
  try { localStorage.setItem("etfLiteView", v); } catch (e) {}
  document.querySelectorAll("#viewtoggle button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === VIEW));
  updateViewHelp();
  renderMeta();
  render();
}

// -- routing ----------------------------------------------------------------
function render() {
  // Preserve filter-box focus + caret across the re-render.
  const active = document.activeElement;
  const wasFilter = active && active.id === "filterInput";
  const caret = wasFilter ? active.selectionStart : null;

  const view = TAB === "cross" ? viewCross()
    : TAB === "coverage" ? viewCoverage()
    : TAB === "guide" ? viewGuide()
    : viewDeltas();
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
  document.querySelectorAll("#viewtoggle button").forEach((b) =>
    b.addEventListener("click", () => setView(b.dataset.view)));
  try {
    const resp = await fetch("data.json", { cache: "no-store" });
    DATA = await resp.json();
  } catch (e) {
    $("#view").replaceChildren(el("div", { class: "empty" }, "Could not load data.json — run the build."));
    return;
  }
  // Resolve initial view: stored preference > payload default > "aligned".
  let stored = null;
  try { stored = localStorage.getItem("etfLiteView"); } catch (e) {}
  VIEW = stored || DATA.default_view || "aligned";
  if (DATA.views && !DATA.views[VIEW]) VIEW = DATA.views.aligned ? "aligned" : VIEW;
  document.querySelectorAll("#viewtoggle button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === VIEW));
  updateViewHelp();
  renderMeta();
  render();
  $("#foot").textContent =
    `etf-flow-lite · ${DATA.source || "web_csv"} · ${(DATA.etfs || []).length} ETFs · click a header to sort · static build`;
}

init();
