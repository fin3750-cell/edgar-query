"""
Pull N years of annual financial data from SEC EDGAR's XBRL companyfacts API.

Free, no API key. SEC asks only that you identify yourself in the User-Agent
header and stay under 10 requests/second.
"""
import os
import time
import threading
import requests

from .tags import (MAP, EXTRA, EPS_TAGS, SHARE_TAGS, MEZZANINE,
                   RATIO_INPUTS, BLOCKS)

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


def _throttle(min_gap=0.12):
    """SEC allows 10 req/sec. Stay well under it."""
    with _rate_lock:
        wait = min_gap - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


def _get(url, timeout=60):
    _throttle()
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r


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


DERIVE = {
    "Gross Profit":
        lambda d, i: _sub(d, "Revenue (Net Sales)", "Cost of Revenue (COGS)", i),
    "Total Liabilities":
        lambda d, i: _sub(d, "Total Assets", "Total Shareholders' Equity", i),
    "Goodwill & Intangible Assets":
        lambda d, i: _add(d, "Goodwill", "IntangibleAssetsNetExcludingGoodwill", i),
    "Operating Income (EBIT)":
        lambda d, i: _add(d, "Pretax Income", "Interest Expense", i),
}


def pull(ticker, nyears=5):
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

    return {
        "ticker": ticker.upper(),
        "name": name,
        "cik": cik,
        "fiscal_years": fys,
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
    missing = [k for k, v in data.items() if all(x is None for x in v)]
    partial = [k for k, v in data.items()
               if any(x is None for x in v) and not all(x is None for x in v)]

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
