"""The 17 web-CSV-accessible ETFs this lite build tracks.

Excludes the 5 funds that can't run unattended: Sprott (SETM, URNM — Cloudflare
403) and VanEck UK UCITS (GDX.L, GDXJ.L, WMIN — website discloses only top-N
holdings daily). Those need a Bloomberg terminal, which CI can't provide.

Parser dispatch is by ``issuer`` (see :mod:`etf_lite.parsers`).
"""

from __future__ import annotations

# Each entry: etf_ticker, issuer, commodity_vertical, fund_page_url.
UNIVERSE: list[dict] = [
    {"etf_ticker": "4COP",     "issuer": "globalx",    "commodity_vertical": "copper",             "fund_page_url": "https://globalxetfs.eu/funds/copx"},
    {"etf_ticker": "COPX",     "issuer": "globalx",    "commodity_vertical": "copper",             "fund_page_url": "https://www.globalxetfs.com/funds/copx"},
    {"etf_ticker": "GDX",      "issuer": "vaneck",     "commodity_vertical": "gold",               "fund_page_url": "https://www.vaneck.com/us/en/investments/gold-miners-etf-gdx/overview/"},
    {"etf_ticker": "GDX-ASX",  "issuer": "vaneck",     "commodity_vertical": "gold",               "fund_page_url": "https://www.vaneck.com.au/etf/equity/gdx/snapshot/"},
    {"etf_ticker": "GDXJ",     "issuer": "vaneck",     "commodity_vertical": "gold",               "fund_page_url": "https://www.vaneck.com/us/en/investments/junior-gold-miners-etf-gdxj/performance/"},
    {"etf_ticker": "IS0E",     "issuer": "ishares",    "commodity_vertical": "gold",               "fund_page_url": "https://www.ishares.com/uk/individual/en/products/251908/ishares-gold-producers-ucits-etf"},
    {"etf_ticker": "LIT",      "issuer": "globalx",    "commodity_vertical": "lithium",            "fund_page_url": "https://www.globalxetfs.com/funds/lit"},
    {"etf_ticker": "MNRS",     "issuer": "betashares", "commodity_vertical": "gold",               "fund_page_url": "https://www.betashares.com.au/fund/global-gold-miners-etf/"},
    {"etf_ticker": "PICK",     "issuer": "ishares",    "commodity_vertical": "diversified_mining", "fund_page_url": "https://www.ishares.com/us/products/239655/ishares-msci-global-metals-mining-producers-etf"},
    {"etf_ticker": "REMX",     "issuer": "vaneck",     "commodity_vertical": "rare_earth",         "fund_page_url": "https://www.vaneck.com/us/en/investments/rare-earth-strategic-metals-etf-remx/"},
    {"etf_ticker": "RING",     "issuer": "ishares",    "commodity_vertical": "gold",               "fund_page_url": "https://www.ishares.com/us/products/239654/ishares-msci-global-gold-miners-etf"},
    {"etf_ticker": "SIL",      "issuer": "globalx",    "commodity_vertical": "silver",             "fund_page_url": "https://www.globalxetfs.com/funds/sil"},
    {"etf_ticker": "SILJ",     "issuer": "amplify",    "commodity_vertical": "silver",             "fund_page_url": "https://amplifyetfs.com/silj/"},
    {"etf_ticker": "SLVP",     "issuer": "ishares",    "commodity_vertical": "silver",             "fund_page_url": "https://www.ishares.com/us/products/239656/ishares-msci-global-silver-and-metals-miners-etf"},
    {"etf_ticker": "URA",      "issuer": "globalx",    "commodity_vertical": "uranium",            "fund_page_url": "https://www.globalxetfs.com/funds/ura"},
    {"etf_ticker": "URNM-ASX", "issuer": "betashares", "commodity_vertical": "uranium",            "fund_page_url": "https://www.betashares.com.au/fund/global-uranium-etf/"},
    {"etf_ticker": "XME",      "issuer": "spdr",       "commodity_vertical": "diversified_mining", "fund_page_url": "https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-metals-mining-etf-xme"},

    # External funds — Sprott is Cloudflare-gated, so CI can't scrape it. These
    # are fed by a desktop browser scraper (scripts/sprott_browser_scrape.py in
    # the main repo) that commits their snapshots here. The CI build SKIPS
    # fetching them (see _fetch_all) but still loads their committed CSVs and
    # computes their deltas, so they appear on the dashboard like any other fund.
    #
    # PAUSED (enabled=False): the desktop capture produced bad rows (e.g. a
    # mislabelled ISIN faking a Taseko removal + phantom addition), so SETM/URNM
    # are toggled OFF the tracker until the capture is fixed. Their committed
    # snapshots stay in git; flip enabled back to True to re-enable.
    {"etf_ticker": "SETM", "issuer": "sprott", "commodity_vertical": "critical_minerals", "fund_page_url": "https://sprottetfs.com/setm-sprott-critical-materials-etf/", "external": True, "enabled": False},
    {"etf_ticker": "URNM", "issuer": "sprott", "commodity_vertical": "uranium",           "fund_page_url": "https://sprottetfs.com/urnm-sprott-uranium-miners-etf/",      "external": True, "enabled": False},
]

