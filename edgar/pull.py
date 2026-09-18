"""
Pull N years of annual financial data from SEC EDGAR's XBRL companyfacts API.

Free, no API key. SEC asks only that you identify yourself in the User-Agent
header and stay under 10 requests/second.
"""
import os
import copy
import time
import threading
import requests

from .tags import (MAP, EXTRA, EPS_TAGS, SHARE_TAGS, MEZZANINE,
                   RATIO_INPUTS, MEMO_ROWS, BLOCKS)

# SEC requires a real contact string. Set SEC_CONTACT in the environment --
# never hardcode a personal address here, this repo is public.
CONTACT = os.environ.get(
    "SEC_CONTACT", "edgar-query educational tool (set SEC_CONTACT)").strip()
UA = {"User-Agent": CONTACT, "Accept-Encoding": "gzip, deflate"}

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{}.json"

_ticker_map = None
_ticker_lock = threading.Lock()
_last_call = [0.0]
_rate_lock = threading.Lock()

# Processed results, keyed by (ticker, nyears). The RAW companyfacts payload
# is deliberately NOT cached: NVDA's is several MB of JSON and many times that
# once parsed, which would exhaust a small instance after a handful of tickers.
# What pull() returns is a few KB and is all either endpoint needs.
FACTS_TTL = 6 * 3600     # annual figures only move when a new 10-K lands
CACHE_MAX = 64
_facts_cache = {}
_facts_lock = threading.Lock()
_counts = [0, 0]         # [hits, misses]


def _throttle(min_gap=0.12):
    """SEC allows 10 req/sec. Stay well under it."""
    with _rate_lock:
        wait = min_gap - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


def _get(url, timeout=60, attempts=3):
    """
    GET with a short backoff on transient failures.

    Connection and DNS errors happen -- a cold Render instance resolving
    www.sec.gov for the first time will occasionally fail outright -- and there
    is no reason to surface that to the caller as a dead company. 429 and 5xx
    are retried too; 404 and other client errors are not.
    """
    last = None
    for i in range(attempts):
        _throttle()
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504) and i < attempts - 1:
                last = requests.HTTPError("{} from {}".format(r.status_code, url))
                time.sleep(0.6 * (2 ** i))
                continue
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException as exc:
            last = exc
            if isinstance(exc, requests.exceptions.HTTPError):
                raise                       # a real 4xx -- do not hammer SEC
            if i < attempts - 1:
                time.sleep(0.6 * (2 ** i))
    raise last


def ticker_to_cik(ticker):
    """Resolve a ticker to (zero-padded CIK, registrant name)."""
    global _ticker_map
    with _ticker_lock:
        if _ticker_map is None:
            j = _get(TICKERS_URL, timeout=30).json()
            _ticker_map = {v["ticker"].upper(): (str(v["cik_str"]).zfill(10), v["title"])
                           for v in j.values()}
    hit = _ticker_map.get(ticker.strip().upper())
    if not hit:
        raise LookupError("Ticker not found in SEC's company list: " + ticker)
    return hit


def fiscal_year_of(end_date):
    """
    The fiscal year a figure belongs to is the year its period ends -- except
    for companies whose year ends early in the calendar year. Retailers
    (Target, Walmart, Home Depot) close FY2023 in Jan/Feb 2024.
    """
    y, m = int(end_date[:4]), int(end_date[5:7])
    return y - 1 if m <= 6 else y


def period_ends(facts, fys):
    """
    {fiscal_year: period end date} taken from the Total Assets series.

    Split adjustment needs to know when each fiscal year actually closed, not
    just which calendar year it belongs to.
    """
    node = facts.get("Assets")
    if not node:
        return {}
    out = {}
    for e in node.get("units", {}).get("USD", []):
        if e.get("form") not in ("10-K", "10-K/A") or e.get("fp") != "FY":
            continue
        if e.get("start"):
            continue
        y = fiscal_year_of(e["end"])
        if y in fys and (y not in out or e["end"] > out[y]):
            out[y] = e["end"]
    return out


def annual_series(facts, tag, kind, fys):
    """{fiscal_year: value} for one tag, annual 10-K figures only."""
    node = facts.get(tag)
    if not node:
        return {}
    units = node.get("units", {})
    key = "USD" if "USD" in units else next(iter(units), None)
    if not key:
        return {}
    out = {}
    for e in units[key]:
        if e.get("form") not in ("10-K", "10-K/A"):
            continue
        if e.get("fp") != "FY" or e.get("fy") is None:
            continue
        if kind == "dur":
            if not e.get("start"):
                continue
            y0, m0 = int(e["start"][:4]), int(e["start"][5:7])
            y1, m1 = int(e["end"][:4]), int(e["end"][5:7])
            months = (y1 - y0) * 12 + (m1 - m0)
            if months < 10 or months > 14:      # full years only
                continue
        elif e.get("start"):                     # instant facts have no start
            continue
        year = fiscal_year_of(e["end"])
        if year not in fys:
            continue
        # Later filings restate. Prefer the earliest original report.
        prev = out.get(year)
        if prev is None or e.get("fy", 9999) < prev[1]:
            out[year] = (e["val"], e.get("fy", 9999))
    return {k: v[0] for k, v in out.items()}


def resolve_row(facts, tags, kind, fys):
    """
    Fill a row year by year, walking the tag priority list independently for
    each year.

    This is the difference from picking one winning tag for the whole row. A
    company that changed presentation mid-window -- Target moved from
    PropertyPlantAndEquipmentNet to the finance-lease-inclusive tag in FY2023 --
    has complete data under no single tag, but complete data across two.

    Returns (values, provenance), both indexed like fys.
    """
    cache = {t: annual_series(facts, t, kind, set(fys)) for t in tags}
    values, prov = [], []
    for y in fys:
        for t in tags:
            v = cache[t].get(y)
            if v is not None:
                values.append(v)
                prov.append(t)
                break
        else:
            values.append(None)
            prov.append(None)
    return values, prov


def _first_available(facts, tag_kinds, fys):
    """
    Walk (tag, kind) candidates per year, like resolve_row but across tags that
    do not share a single 'kind'. Returns (values, provenance).
    """
    cache = [(t, annual_series(facts, t, k, set(fys))) for t, k in tag_kinds]
    values, prov = [], []
    for y in fys:
        for tag, series in cache:
            v = series.get(y)
            if v is not None:
                values.append(v)
                prov.append(tag)
                break
        else:
            values.append(None)
            prov.append(None)
    return values, prov


def _pick(d, key, i):
    seq = d.get(key)
    if not seq or i >= len(seq):
        return None
    return seq[i]


def _sub(d, a, b, i):
    x, y = _pick(d, a, i), _pick(d, b, i)
    return None if x is None or y is None else x - y


def _add(d, a, b, i):
    x, y = _pick(d, a, i), _pick(d, b, i)
    if x is None and y is None:
        return None
    return (x or 0) + (y or 0)


def _gross_ppe(d, i):
    """
    Gross PP&E = Net PP&E + accumulated depreciation, when the filer does not
    tag gross directly.

    Accumulated depreciation is reported as a negative number by some filers and
    a positive one by others, so take the magnitude. This is a fallback: where a
    company changed to the finance-lease-inclusive net tag mid-window but still
    reports plain accumulated depreciation, the two are on slightly different
    bases and the sum is approximate. It is marked (derived) for that reason.
    """
    net = _pick(d, "Property, Plant & Equipment (net)", i)
    acc = _pick(d, "Accumulated Depreciation", i)
    if net is None or acc is None:
        return None
    return net + abs(acc)


DERIVE = {
    "Gross Profit":
        lambda d, i: _sub(d, "Revenue (Net Sales)", "Cost of Revenue (COGS)", i),
    "Total Liabilities":
        lambda d, i: _sub(d, "Total Assets", "Total Shareholders' Equity", i),
    "Goodwill & Intangible Assets":
        lambda d, i: _add(d, "Goodwill", "IntangibleAssetsNetExcludingGoodwill", i),
    "Operating Income (EBIT)":
        lambda d, i: _add(d, "Pretax Income", "Interest Expense", i),
    "Property, Plant & Equipment (gross)": _gross_ppe,
}


def _pull_fresh(ticker, nyears=5):
    """Return a dict of everything the app needs for one company."""
    cik, name = ticker_to_cik(ticker)
    payload = _get(FACTS_URL.format(cik), timeout=90).json()
    facts = payload.get("facts", {})
    gaap = facts.get("us-gaap", {})
    dei = facts.get("dei", {})

    probe = annual_series(gaap, "Assets", "inst", set(range(1990, 2200)))
    if not probe:
        raise ValueError(
            "No annual Total Assets data for {}. It may not be a 10-K filer "
            "(foreign issuers file 20-F, funds file N-CSR).".format(ticker.upper()))
    latest = max(probe)
    fys = list(range(latest - nyears + 1, latest + 1))

    data, prov = {}, {}
    for label, kind, tags in MAP:
        data[label], prov[label] = resolve_row(gaap, tags, kind, fys)

    extras = {}
    for tag, kind in EXTRA:
        extras[tag], _ = resolve_row(gaap, [tag], kind, fys)

    # EPS and its denominator each walk their own priority chain, per year.
    extras["_eps"], extras["_eps_src"] = _first_available(gaap, EPS_TAGS, fys)
    extras["_shares"], extras["_shares_src"] = _first_available(gaap, SHARE_TAGS, fys)

    # Mezzanine ("temporary") equity sits between liabilities and permanent
    # equity. The template has no row for it, so fold it into equity -- what
    # commercial data providers do -- and report that we did.
    mezz, _ = resolve_row(gaap, MEZZANINE, "inst", fys)
    mezz_note = None
    if any(v is not None for v in mezz):
        te = data["Total Shareholders' Equity"]
        data["Total Shareholders' Equity"] = [
            None if te[i] is None else te[i] + (mezz[i] or 0) for i in range(len(fys))]
        mezz_note = "Temporary (mezzanine) equity folded into Total Equity: " + ", ".join(
            "FY{} {:,.0f}".format(fys[i], mezz[i]) for i in range(len(fys))
            if mezz[i] is not None)

    merged = dict(data)
    merged.update(extras)
    for label, fn in DERIVE.items():
        vals = data.get(label)
        if not vals or not any(v is None for v in vals):
            continue
        filled = [vals[i] if vals[i] is not None else fn(merged, i)
                  for i in range(len(fys))]
        if any(v is not None for v in filled):
            for i in range(len(fys)):
                if vals[i] is None and filled[i] is not None:
                    prov[label][i] = "(derived)"
            data[label] = filled

    # Interest expense is a cost; the template wants it positive.
    data["Interest Expense"] = [None if v is None else abs(v)
                                for v in data["Interest Expense"]]

    shares, shares_asof = _shares_outstanding(dei)
    pends = period_ends(gaap, set(fys))

    return {
        "ticker": ticker.upper(),
        "name": name,
        "cik": cik,
        "fiscal_years": fys,
        "period_ends": [pends.get(y) for y in fys],
        "data": data,
        "provenance": prov,
        "extras": extras,
        "shares_outstanding": shares,
        "shares_asof": shares_asof,
        "mezzanine_note": mezz_note,
        "diagnostics": diagnose(data, fys),
    }


def _shares_outstanding(dei):
    """Latest cover-page share count and the date it was measured."""
    node = dei.get("EntityCommonStockSharesOutstanding")
    if not node:
        return None, None
    best = None
    for arr in node.get("units", {}).values():
        for e in arr:
            if best is None or e["end"] > best["end"]:
                best = e
    return (best["val"], best["end"]) if best else (None, None)


def pull(ticker, nyears=5):
    """
    Cached wrapper around _pull_fresh().

    A class all analysing the same handful of companies is the common case, and
    an annual figure does not change between two students loading it. Repeat
    lookups are served from memory; only the first pays for EDGAR.

    Deliberately NOT cached alongside this: the share price. app.py fetches that
    separately through market.quote(), which keeps its own 15-minute TTL, so a
    cached pull still yields a current P/E.

    A copy goes out on every hit -- callers that mutate what they are handed
    must not corrupt the cached entry.
    """
    key = (ticker.strip().upper(), nyears)
    now = time.monotonic()

    with _facts_lock:
        hit = _facts_cache.get(key)
        if hit and now - hit[0] < FACTS_TTL:
            _counts[0] += 1
            return copy.deepcopy(hit[1])
        _counts[1] += 1

    # Fetched outside the lock: a slow EDGAR round trip must not block readers
    # already holding cached tickers. Two students racing the same cold ticker
    # may both fetch it; that costs one extra request and is cheaper than
    # serialising every lookup behind one mutex.
    result = _pull_fresh(ticker, nyears)

    with _facts_lock:
        if len(_facts_cache) >= CACHE_MAX:
            oldest = min(_facts_cache, key=lambda k: _facts_cache[k][0])
            del _facts_cache[oldest]
        _facts_cache[key] = (now, result)

    return copy.deepcopy(result)


def cache_stats():
    """Counters for /healthz -- confirms the cache is actually being used."""
    with _facts_lock:
        return {"entries": len(_facts_cache), "hits": _counts[0],
                "misses": _counts[1], "ttl_seconds": FACTS_TTL}


def cache_clear():
    """Drop every entry. For picking up a fresh filing without a redeploy."""
    with _facts_lock:
        n = len(_facts_cache)
        _facts_cache.clear()
    return n


def diagnose(data, fys):
    """Balance check, ratio readiness, and suitability for this template."""
    balance = []
    for i, y in enumerate(fys):
        ta = data["Total Assets"][i]
        tl = data["Total Liabilities"][i]
        te = data["Total Shareholders' Equity"][i]
        if None in (ta, tl, te):
            balance.append({"year": y, "status": "missing", "diff": None})
            continue
        diff = ta - (tl + te)
        tol = max(1.0, abs(ta) * 2e-5)
        balance.append({"year": y,
                        "status": "ok" if abs(diff) <= tol else "fail",
                        "diff": diff})

    gaps = [r for r in RATIO_INPUTS if all(v is None for v in data.get(r, [None]))]
    missing = [k for k, v in data.items()
               if k not in MEMO_ROWS and all(x is None for x in v)]
    partial = [k for k, v in data.items()
               if k not in MEMO_ROWS
               and any(x is None for x in v) and not all(x is None for x in v)]

    unclassified = [r for r in ("Total Current Assets", "Total Current Liabilities")
                    if all(v is None for v in data.get(r, [None]))]
    note = None
    if unclassified:
        note = ("This company does not present a classified balance sheet, so it "
                "has no {}. Normal for firms with a captive finance arm (Deere, "
                "Ford, GE) and for banks and insurers. Liquidity ratios cannot "
                "be computed.".format(" or ".join(unclassified)))

    return {
        "balance": balance,
        "reconciles": all(b["status"] == "ok" for b in balance),
        "ratio_inputs_present": len(RATIO_INPUTS) - len(gaps),
        "ratio_inputs_total": len(RATIO_INPUTS),
        "blocked_ratios": {g: BLOCKS.get(g, "several ratios") for g in gaps},
        "missing_rows": missing,
        "partial_rows": partial,
        "suitable": not unclassified,
        "suitability_note": note,
    }
