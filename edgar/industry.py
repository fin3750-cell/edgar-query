"""
Industry benchmark ratios, looked up by the filer's SIC code.

The medians live in data/industry_ratios.json, built offline by
tools/build_industry.py from SEC's Financial Statement Data Sets. Nothing here
goes near those bulk files at request time -- the only network call is one
lookup of the company's own SIC code, cached for the life of the process.

Everything fails soft, the way market.py does. No JSON file, no SIC, too few
peers: the workbook drops the benchmark column and says why on the Sources tab.
It never blocks a download.
"""
import os
import json
import time
import threading

from .pull import _get

_DATA = os.path.join(os.path.dirname(__file__), "..", "data")
TABLE = os.path.join(_DATA, "industry_ratios.json")
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{}.json"

# Below this the median is a coin toss rather than a benchmark. SIC is granular
# enough that dozens of codes carry two or three public filers, which is why
# the table stores the three- and two-digit rollups too.
MIN_PEERS = 5

# Per-ratio floor. A SIC can have thirty filers and still only eight that report
# an inventory, and the reader needs to know which of the two they are looking
# at -- so the count travels with every number.
MIN_PER_RATIO = 3

_table = None
_table_err = None
_table_lock = threading.Lock()

SIC_TTL = 30 * 86400            # a company's SIC changes about never
_sic_cache = {}
_sic_lock = threading.Lock()


def table():
    """The parsed JSON, or None if it is missing or unreadable."""
    global _table, _table_err
    with _table_lock:
        if _table is not None or _table_err is not None:
            return _table
        try:
            with open(TABLE, encoding="utf-8") as fh:
                _table = json.load(fh)
        except Exception as exc:                    # noqa: BLE001 - fail soft
            _table_err = "{}: {}".format(type(exc).__name__, exc)
            return None
        return _table


def sic_for(cik):
    """
    {sic, description} for a CIK, from SEC's submissions endpoint.

    companyfacts carries the numbers but not the classification, so this is a
    second request. It is small, cached for a month, and shares pull.py's
    throttle and User-Agent so it counts against the same SEC rate budget.
    """
    key = str(cik)
    now = time.monotonic()
    with _sic_lock:
        hit = _sic_cache.get(key)
        if hit and now - hit[0] < SIC_TTL:
            return hit[1]

    try:
        j = _get(SUBMISSIONS_URL.format(key.zfill(10)), timeout=30).json()
        raw = str(j.get("sic") or "").strip()
        out = {"sic": raw.zfill(4) if raw.isdigit() else None,
               "description": (j.get("sicDescription") or "").strip() or None,
               "error": None}
    except Exception as exc:                        # noqa: BLE001 - fail soft
        out = {"sic": None, "description": None,
               "error": "{}: {}".format(type(exc).__name__, exc)}

    with _sic_lock:
        _sic_cache[key] = (now, out)
    return out


def _level_name(code):
    return {4: "SIC code", 3: "SIC group", 2: "SIC major group"}[len(code)]


def _market_multiples(tbl, sic):
    """
    Industry P/E and P/B, keyed to the workbook's market-block row labels.

    Kept apart from `ratios` on purpose. Those are medians we computed with the
    template's own formulas; these are aggregates from a different author over a
    different universe. They share a sheet, not a column, and the block that
    prints them says whose they are.
    """
    out = {"available": False}
    block = (tbl or {}).get("market_multiples") or {}
    entry = (block.get("by_sic") or {}).get(sic or "")
    if not entry:
        return out
    pairs = {label: entry[key]
             for label, key in (("Price / Earnings (P/E)", "P/E"),
                                ("Market / Book (P/B)", "P/B"))
             if entry.get(key) is not None}
    if not pairs:
        return out
    return {"available": True, "ratios": pairs,
            "industry": entry.get("industry"), "firms": entry.get("firms"),
            "source": block.get("source"), "source_url": block.get("source_url"),
            "updated": block.get("updated"), "method": block.get("method")}


def benchmark(cik):
    """
    {available, ratios: {label: {median, n}}, ...} for one company's industry.

    Widens from the exact SIC to the three- and two-digit rollup until it finds
    a bucket with enough filers behind it. Which level it settled on is part of
    the answer, not an implementation detail -- "the median of 7 filers sharing
    a two-digit major group" is a weaker benchmark than "the median of 41
    sharing an exact SIC", and the workbook prints both the level and the count
    so the reader can discount accordingly.
    """
    out = {"available": False, "sic": None, "sic_description": None,
           "code": None, "level": None, "name": None, "filers": 0,
           "ratios": {}, "built": None, "quarters": [], "note": None,
           "market": {"available": False}}

    tbl = table()
    if tbl is None:
        out["note"] = ("No industry table on disk ({}). Build it with "
                       "tools/build_industry.py.".format(_table_err))
        return out

    out["built"] = tbl.get("built")
    out["quarters"] = tbl.get("quarters", [])

    found = sic_for(cik)
    out["sic"] = found["sic"]
    out["sic_description"] = found["description"]
    if not found["sic"]:
        out["note"] = ("SEC does not report a SIC code for this filer"
                       if found["error"] is None else
                       "SIC lookup failed ({})".format(found["error"]))
        return out

    out["market"] = _market_multiples(tbl, found["sic"])

    industries = tbl.get("industries", {})
    sic = found["sic"]
    for code in (sic, sic[:3], sic[:2]):
        entry = industries.get(code)
        if entry and entry.get("filers", 0) >= MIN_PEERS:
            out["available"] = True
            out["code"] = code
            out["level"] = _level_name(code)
            # At the exact code, SEC's own description of this company's
            # industry is better English than the table's copy of it.
            out["name"] = (found["description"] or entry.get("name")
                           if len(code) == 4 else entry.get("name"))
            out["filers"] = entry["filers"]
            out["ratios"] = {k: v for k, v in entry.get("ratios", {}).items()
                             if v.get("n", 0) >= MIN_PER_RATIO}
            return out

    out["note"] = (
        "Fewer than {} 10-K filers with a classified balance sheet share this "
        "company's industry (SIC {}), so no median would mean much. Banks, "
        "insurers and firms with a captive finance arm are held out of the peer "
        "set for the same reason the template does not fit them."
        .format(MIN_PEERS, sic))
    return out
