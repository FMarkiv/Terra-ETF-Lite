# etf-flow-lite

A stripped-down, **GitHub-runnable** ETF holdings flow tracker for mining &
resources funds. Every weekday it scrapes the 17 ETFs whose holdings are public
on the provider website, computes day-over-day **flow deltas** (additions,
removals, weight/share changes) and **cross-ETF consensus** (the same name being
moved by several funds at once), and publishes a static dashboard to GitHub
Pages. No Bloomberg, no database server, no always-on machine.

This is a self-contained extract of the full ETF Holdings Tracker — the
ingestion + delta logic is ported verbatim, so the numbers match. The full
tracker's BLPAPI/BQL paths, live FastAPI dashboard and Telegram alert are
removed (Telegram can be added back later — see below).

## The 17 ETFs

Gold: GDX, GDXJ, RING, IS0E, MNRS, GDX-ASX · Silver: SIL, SILJ, SLVP ·
Copper: COPX, 4COP · Uranium: URA, URNM-ASX · Diversified: XME, PICK ·
Rare earth: REMX · Lithium: LIT

Five funds from the full universe are **excluded** because they can't be scraped
unattended: Sprott SETM & URNM (Cloudflare 403) and VanEck UK UCITS GDX.L,
GDXJ.L, WMIN (website discloses only top-N holdings daily). Those need a
Bloomberg terminal, which CI can't provide.

## How it works

```
GitHub Action (daily cron)
  └─ python -m etf_lite.build
       ├─ scrape 17 ETFs            (etf_lite/parsers/*, one per issuer)
       ├─ normalise + resolve ISINs (etf_lite/normaliser, isin_resolver)
       ├─ append today's snapshot   -> data/snapshots/{TICKER}.csv   (committed back)
       ├─ load all snapshots into in-memory DuckDB
       ├─ compute deltas + cross-ETF (etf_lite/queries, engine, cross_etf)
       └─ render static dashboard   -> site/  (index.html + data.json)
  └─ commit data/snapshots, deploy site/ to GitHub Pages
```

**Why CSVs in the repo?** Actions runners are ephemeral, but deltas need
*yesterday's* holdings. The append-only `data/snapshots/*.csv` files are the
persistent store — committed back each run, versioned for free, and rebuilt into
DuckDB in-memory at delta time. Dedup is at the snapshot grain: re-running on a
day whose as-of date is already stored is a no-op.

Cash sleeves and FX/residual rows are kept in the snapshots (informative) but
excluded from the delta computation, so the dashboard shows real flow only.

## Run locally

```bash
pip install -r requirements.txt
python -m etf_lite.build
# open the result:
python -m http.server -d site 8000   # then visit http://localhost:8000
```

The first run shows everything as "additions" (no prior day to compare yet);
from the second day on you get real deltas.

## Deploy on GitHub

1. Push this folder as its own repository.
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. The workflow (`.github/workflows/daily.yml`) runs **Tue–Sat 07:00
   Australia/Sydney** (Mon–Fri 21:00 UTC) and on manual dispatch. By that hour
   the previous US close has been published overnight (AEST evening) and the ASX
   files are in, so each run captures the freshest holdings; US doesn't trade
   over the weekend, so Sun/Mon AEST are skipped.
4. First run: trigger it manually from the **Actions** tab to seed history and
   publish the page. The dashboard URL appears in the `deploy` job summary.

The workflow needs no secrets. It uses the built-in `GITHUB_TOKEN` to commit
snapshots (`contents: write`) and publish Pages (`pages: write`).

## Adding Telegram later

The build produces a `DeltaResult` (see `etf_lite/engine.py`) that already has
everything an alert needs. To add the morning push: format that result and post
it to the Telegram Bot API in a new build step, with `TELEGRAM_BOT_TOKEN` /
`TELEGRAM_CHAT_ID` stored as GitHub Actions secrets. No other changes required.

## Layout

```
etf_lite/
  universe.py          the 17 ETFs (ticker, issuer, vertical, fund page)
  parsers/             one web-CSV parser per issuer + shared helpers
  normaliser.py        canonical-schema normalisation + cash tagging
  isin_resolver.py     ticker/name -> ISIN crosswalk (built per-run)
  queries.py           the delta SQL (cash/FX excluded from deltas)
  cross_etf.py         cross-fund consensus aggregation
  engine.py            delta computation + threshold filtering
  store.py             CSV snapshot history <-> in-memory DuckDB
  build.py             daily entry point
config/                thresholds.yaml, isin_overrides.yaml, (manual_urls.yaml)
frontend/              static dashboard (index.html, styles.css, app.js)
data/snapshots/        committed CSV history (the persistent store)
```
