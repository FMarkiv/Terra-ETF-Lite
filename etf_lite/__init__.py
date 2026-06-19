"""etf-flow-lite — a stripped-down, CI-friendly ETF holdings flow tracker.

Scrapes the 17 web-CSV-accessible mining/resources ETFs, computes day-over-day
holdings deltas + cross-ETF consensus, and renders a static dashboard. No
Bloomberg, no database server, no always-on host — built to run on a daily
GitHub Action and publish to GitHub Pages.

The ingestion + delta logic is ported verbatim from the full ETF Holdings
Tracker so the numbers match; this package just removes the BLPAPI/BQL paths,
the live FastAPI server and the Telegram delivery (Telegram can be added later).
"""
