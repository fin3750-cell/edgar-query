"""
Live market data: share price.

EDGAR has no prices. Yahoo's chart endpoint is free and needs no key, but it is
unofficial -- cloud IPs get throttled harder than residential ones. Everything
here fails soft: a missing price blanks the market ratios and leaves every
EDGAR-sourced ratio intact.

Stooq was the obvious alternative and is no longer usable: its CSV endpoints
404 and the site now sits behind a JavaScript proof-of-work challenge.
"""
import time
import threading
import requests

QUOTE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}"

# Yahoo rejects requests without a browser-shaped User-Agent.
UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/125.0 Safari/537.36")}

TTL = 900          # 15 minutes; intraday moves do not change a ratio's story
_cache = {}
_lock = threading.Lock()


def quote(ticker):
    """
    {price, currency, as_of, source} or {price: None, error: "..."}.
    Never raises -- the caller should still render the EDGAR ratios.
    """
    key = ticker.strip().upper()
    now = time.monotonic()

    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < TTL:
            return hit[1]

    try:
        r = requests.get(QUOTE_URL.format(key), headers=UA,
                         params={"range": "1d", "interval": "1d"}, timeout=12)
        r.raise_for_status()
        meta = r.json()["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        if price is None:
            raise ValueError("no regularMarketPrice in response")
        out = {
            "price": float(price),
            "currency": meta.get("currency", "USD"),
            "exchange": meta.get("exchangeName"),
            "as_of": meta.get("regularMarketTime"),
            "source": "Yahoo Finance",
            "error": None,
        }
    except Exception as exc:                       # noqa: BLE001 - fail soft
        out = {"price": None, "currency": None, "exchange": None,
               "as_of": None, "source": "Yahoo Finance",
               "error": "{}: {}".format(type(exc).__name__, exc)}

    with _lock:
        _cache[key] = (now, out)
    return out
