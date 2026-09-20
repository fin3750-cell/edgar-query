"""
Build data/industry_ratios.json -- industry median ratios, by SIC code.

Run offline, not at request time:

    python tools/build_industry.py                  # last four quarters
    python tools/build_industry.py 2025q3 2025q4 2026q1 2026q2

Source is SEC's Financial Statement Data Sets -- the same XBRL facts behind
companyfacts, published quarterly as a bulk download. Four consecutive quarters
covers roughly one 10-K per filer, whatever month their fiscal year ends.

The point of computing the benchmark here rather than taking a published one is
that it goes through the SAME code as the company column: tags.MAP for tag
priority, pull.DERIVE for the derived rows, ratios.SPEC for the formulas. A
published industry average -- Damodaran's, say -- aggregates numerators and
denominators across the industry, uses average rather than period-end balances,
and lease-adjusts. Those are defensible choices, but they are not the
template's, and a benchmark that does not share the formula it is benchmarking
is a number that looks comparable and is not.

Re-run it when a new quarter lands (SEC posts one about a month after quarter
end) and commit the JSON. Nothing at request time touches these ZIPs.
"""
import io
import os
import sys
import csv
import json
import zipfile
import datetime
import statistics
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from edgar.tags import MAP, MEZZANINE                      # noqa: E402
from edgar.pull import DERIVE, CONTACT, fiscal_year_of     # noqa: E402
from edgar import ratios                                   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
OUT = os.path.join(ROOT, "data", "industry_ratios.json")
# Roughly 350 MB of ZIPs for four quarters. .cache/ is gitignored; point
# FSDS_CACHE somewhere else to keep them off the project disk entirely.
CACHE = os.environ.get("FSDS_CACHE") or os.path.join(ROOT, ".cache", "fsds")

FSDS_URL = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/{}.zip"
SIC_LIST_URL = ("https://www.sec.gov/search-filings/"
                "standard-industrial-classification-sic-code-list")

# A filer has to look like an operating company to count. Shells, blank-check
# vehicles and pre-revenue biotechs file 10-Ks by the hundred, and a margin
# median that includes them describes the filing population rather than the
# industry.
MIN_ASSETS = 1e6
MIN_REVENUE = 1e6

# Tags we care about, so the num.txt scan can throw away most of each file
# without parsing it into anything.
WANTED_TAGS = {}                    # tag -> "inst" | "dur"
for _label, _kind, _tags in MAP:
    for _t in _tags:
        WANTED_TAGS[_t] = _kind
for _t in MEZZANINE:
    WANTED_TAGS[_t] = "inst"

QTRS_FOR = {"inst": "0", "dur": "4"}


def _fetch(url, dest=None, timeout=300):
    req = urllib.request.Request(url, headers={"User-Agent": CONTACT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    if dest:
        with open(dest, "wb") as fh:
            fh.write(body)
    return body


def recent_quarters(n=4):
    """The n most recent quarters SEC has actually published."""
    today = datetime.date.today()
    y, q = today.year, (today.month - 1) // 3 + 1
    out = []
    while len(out) < n:
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        tag = "{}q{}".format(y, q)
        req = urllib.request.Request(FSDS_URL.format(tag), method="HEAD",
                                     headers={"User-Agent": CONTACT})
        try:
            urllib.request.urlopen(req, timeout=60).close()
        except Exception:                                   # noqa: BLE001
            continue                                        # not posted yet
        out.append(tag)
    return list(reversed(out))


def ensure_zip(quarter):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, quarter + ".zip")
    if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
        return path
    print("  downloading {} ...".format(quarter), flush=True)
    _fetch(FSDS_URL.format(quarter), dest=path)
    return path


def _reader(zf, name):
    """Tab-separated member as dicts. Field order has changed between vintages,
    so everything goes through the header rather than a positional index."""
    with zf.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        for row in csv.DictReader(text, delimiter="\t"):
            yield row


def read_quarter(path, filings):
    """
    Merge one quarter's 10-K facts into `filings`, keyed by CIK.

    Two rules mirror pull.py. The latest fiscal year wins, because a CIK can
    appear in more than one of the four quarters. Within one period the earliest
    filing wins, so an original 10-K beats the 10-K/A that restates it.
    """
    with zipfile.ZipFile(path) as zf:
        subs = {}
        for s in _reader(zf, "sub.txt"):
            if s.get("form") not in ("10-K", "10-K/A"):
                continue
            if s.get("prevrpt") == "1":         # superseded by a later filing
                continue
            sic, period, cik = s.get("sic"), s.get("period"), s.get("cik")
            if not (sic and period and cik) or not str(sic).strip().isdigit():
                continue
            subs[s["adsh"]] = {
                "cik": str(int(cik)),
                "name": s.get("name", ""),
                "sic": str(sic).strip().zfill(4),
                "period": period.strip(),
                "filed": s.get("filed", "99999999"),
                "vals": {},
            }
        print("    {} 10-K filings".format(len(subs)), flush=True)

        # num.txt runs to half a gigabyte a quarter and roughly eight million
        # rows, so this loop is index-based rather than dict-based -- building a
        # DictReader row for every fact costs more than everything else here put
        # together. The header still decides the indices; only the lookup per
        # row is positional.
        kept = 0
        with zf.open("num.txt") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
            rd = csv.reader(text, delimiter="\t")
            head = next(rd)
            i_adsh, i_tag, i_ver = head.index("adsh"), head.index("tag"), head.index("version")
            i_dd, i_q, i_uom = head.index("ddate"), head.index("qtrs"), head.index("uom")
            i_seg, i_cor, i_val = head.index("segments"), head.index("coreg"), head.index("value")
            width = len(head)

            for row in rd:
                if len(row) != width:
                    continue
                sub = subs.get(row[i_adsh])
                if sub is None:
                    continue
                kind = WANTED_TAGS.get(row[i_tag])
                if kind is None:
                    continue
                # Consolidated totals only: a segment or co-registrant breakdown
                # is a slice of the company, not the company.
                if row[i_seg] or row[i_cor]:
                    continue
                if row[i_uom] != "USD" or not row[i_ver].startswith("us-gaap"):
                    continue
                if row[i_q] != QTRS_FOR[kind] or row[i_dd] != sub["period"]:
                    continue
                if not row[i_val]:
                    continue
                try:
                    sub["vals"][row[i_tag]] = float(row[i_val])
                except ValueError:
                    continue
                kept += 1
        print("    {} facts kept".format(kept), flush=True)

    for sub in subs.values():
        if not sub["vals"]:
            continue
        prev = filings.get(sub["cik"])
        if prev is None or sub["period"] > prev["period"] or (
                sub["period"] == prev["period"] and sub["filed"] < prev["filed"]):
            filings[sub["cik"]] = sub
    return filings


def company_ratios(vals):
    """
    One filer's 15 ratios, through the same path as the company column.

    Values are single-element lists because that is the shape pull.py produces
    and DERIVE and ratios.compute both index into -- reusing them is the whole
    point, so the shape is worth humouring.
    """
    data = {}
    for label, _kind, tags in MAP:
        v = next((vals[t] for t in tags if t in vals), None)
        data[label] = [v]

    mezz = next((vals[t] for t in MEZZANINE if t in vals), None)
    if mezz is not None and data["Total Shareholders' Equity"][0] is not None:
        data["Total Shareholders' Equity"] = [
            data["Total Shareholders' Equity"][0] + mezz]

    for label, fn in DERIVE.items():
        if data.get(label, [None])[0] is None:
            data[label] = [fn(data, 0)]

    if data["Interest Expense"][0] is not None:
        data["Interest Expense"] = [abs(data["Interest Expense"][0])]

    assets = data["Total Assets"][0]
    revenue = data["Revenue (Net Sales)"][0]
    if assets is None or assets < MIN_ASSETS:
        return None
    if revenue is None or revenue < MIN_REVENUE:
        return None

    # Same suitability test the app applies to the company itself: no current
    # asset subtotal means no classified balance sheet, and the template's rows
    # do not describe the filer. Letting them in does not just blank the
    # liquidity ratios -- a bank's revenue row picks up fee income and misses
    # net interest income, so the SIC 6021 median net margin came out at 193%,
    # which is a formula meeting the wrong kind of company, not a benchmark.
    if data["Total Current Assets"][0] is None or data["Total Current Liabilities"][0] is None:
        return None

    rows = ratios.compute(data, [0])
    out = {r["label"]: r["values"][0] for r in rows}

    # Negative book equity makes three of these arithmetic rather than
    # informative -- a D/E of -4 sits below a healthy 0.8 in any sort, and one
    # dragged the median for a whole SIC before this guard went in.
    if (data["Total Shareholders' Equity"][0] or 0) <= 0:
        for label in ("Debt-to-Equity", "Equity Multiplier", "Return on Equity (ROE)"):
            out[label] = None
    return out


def sic_names():
    try:
        import re
        import html
        body = _fetch(SIC_LIST_URL, timeout=120).decode("utf-8", "replace")
    except Exception as exc:                                # noqa: BLE001
        print("  SIC name list unavailable ({}), codes will go unlabelled".format(exc))
        return {}
    out = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
        cells = [html.unescape(re.sub("<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        if len(cells) >= 3 and cells[0].isdigit():
            out[cells[0].zfill(4)] = cells[2].title()
    return out


LABELS = [label for label, _g, _n, _d, _f in ratios.SPEC]


def aggregate(filings, names):
    """
    Median per ratio at three levels of SIC: the exact code, the three-digit
    group, the two-digit major group.

    The rollups are not a nicety. SIC is fine-grained enough that plenty of
    codes carry two or three public filers, and a "median" of two is a coin
    toss. Storing all three levels lets the reader pick the narrowest one that
    has enough filers behind it to mean something.
    """
    buckets = {}
    for cik, sub in filings.items():
        r = company_ratios(sub["vals"])
        if r is None:
            continue
        sic = sub["sic"]
        for code in (sic, sic[:3], sic[:2]):
            b = buckets.setdefault(code, {"ciks": set(), "vals": {k: [] for k in LABELS}})
            b["ciks"].add(cik)
            for label in LABELS:
                if r[label] is not None:
                    b["vals"][label].append(r[label])

    industries = {}
    for code, b in buckets.items():
        entry = {"filers": len(b["ciks"]), "ratios": {}}
        if len(code) == 4:
            entry["name"] = names.get(code) or "SIC {}".format(code)
        else:
            entry["name"] = "SIC {}{} group".format(code, "x" * (4 - len(code)))
        for label, series in b["vals"].items():
            if series:
                entry["ratios"][label] = {"median": round(statistics.median(series), 6),
                                          "n": len(series)}
        industries[code] = entry
    return industries


def main(argv):
    quarters = argv[1:] or recent_quarters(4)
    print("quarters: {}".format(", ".join(quarters)))

    filings = {}
    for q in quarters:
        print("  {}".format(q), flush=True)
        read_quarter(ensure_zip(q), filings)
    print("{} filers with usable facts".format(len(filings)))

    industries = aggregate(filings, sic_names())
    payload = {
        "built": datetime.date.today().isoformat(),
        "source": "SEC Financial Statement Data Sets",
        "source_url": FSDS_URL.format("QUARTER"),
        "quarters": quarters,
        "method": ("Median across filers of each ratio, computed from as-filed 10-K "
                   "figures through the same tag priority and formulas the company "
                   "column uses. One 10-K per filer, the most recent in the window."),
        "ratios": LABELS,
        "industries": industries,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"), sort_keys=True)

    sized = sum(1 for e in industries.values() if e["filers"] >= 5)
    print("wrote {} ({:.0f} KB): {} codes, {} with 5+ filers".format(
        os.path.relpath(OUT, ROOT), os.path.getsize(OUT) / 1024,
        len(industries), sized))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
