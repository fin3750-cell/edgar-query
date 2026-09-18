"""
The fourteen ratios from the Module 05 template, the DuPont decomposition, and
the market ratios EDGAR alone cannot produce.

Formulas mirror the template's Ratios sheet exactly, including its choices:
period-end balances rather than averages, and Net Income (not EAC) in ROA/ROE.
Keep them that way -- the point is that the app and the spreadsheet agree.
"""

# label, group, numerator(s), denominator, format
SPEC = [
    ("Current Ratio",         "LIQUIDITY",      "Total Current Assets",             "Total Current Liabilities", "x"),
    ("Quick Ratio",           "LIQUIDITY",      ("Total Current Assets", "Inventory"), "Total Current Liabilities", "x"),
    ("Cash Ratio",            "LIQUIDITY",      "Cash & Short-Term Investments",    "Total Current Liabilities", "x"),
    ("Debt Ratio",            "LEVERAGE",       "Total Liabilities",                "Total Assets",              "x"),
    ("Debt-to-Equity",        "LEVERAGE",       "Total Liabilities",                "Total Shareholders' Equity", "x"),
    ("Equity Multiplier",     "LEVERAGE",       "Total Assets",                     "Total Shareholders' Equity", "x"),
    ("Times Interest Earned", "LEVERAGE",       "Operating Income (EBIT)",          "Interest Expense",          "x"),
    ("Gross Margin",          "PROFITABILITY",  "Gross Profit",                     "Revenue (Net Sales)",       "%"),
    ("Operating Margin",      "PROFITABILITY",  "Operating Income (EBIT)",          "Revenue (Net Sales)",       "%"),
    ("Net Profit Margin",     "PROFITABILITY",  "Net Income",                       "Revenue (Net Sales)",       "%"),
    ("Return on Assets (ROA)", "PROFITABILITY", "Net Income",                       "Total Assets",              "%"),
    ("Return on Equity (ROE)", "PROFITABILITY", "Net Income",                       "Total Shareholders' Equity", "%"),
    ("Inventory Turnover",    "EFFICIENCY",     "Cost of Revenue (COGS)",           "Inventory",                 "x"),
    ("Total Asset Turnover",  "EFFICIENCY",     "Revenue (Net Sales)",              "Total Assets",              "x"),
]


def _val(data, key, i):
    seq = data.get(key)
    if not seq or i >= len(seq):
        return None
    return seq[i]


def _div(num, den):
    if num is None or den is None or den == 0:
        return None
    return num / den


def compute(data, fys):
    """[{label, group, format, values: [...]}] in template order."""
    out = []
    for label, group, num, den, fmt in SPEC:
        values = []
        for i in range(len(fys)):
            if isinstance(num, tuple):
                a, b = _val(data, num[0], i), _val(data, num[1], i)
                n = None if a is None or b is None else a - b
            else:
                n = _val(data, num, i)
            values.append(_div(n, _val(data, den, i)))
        out.append({"label": label, "group": group, "format": fmt, "values": values})
    return out


def dupont(rows, fys):
    """
    ROE = Net Profit Margin x Total Asset Turnover x Equity Multiplier.

    The check row is the template's whole point: if it is not zero, one of the
    three components is wrong.
    """
    by = {r["label"]: r["values"] for r in rows}
    npm = by.get("Net Profit Margin", [None] * len(fys))
    tat = by.get("Total Asset Turnover", [None] * len(fys))
    em = by.get("Equity Multiplier", [None] * len(fys))
    roe = by.get("Return on Equity (ROE)", [None] * len(fys))

    derived, check = [], []
    for i in range(len(fys)):
        if None in (npm[i], tat[i], em[i]):
            derived.append(None)
            check.append(None)
            continue
        d = npm[i] * tat[i] * em[i]
        derived.append(d)
        check.append(None if roe[i] is None else d - roe[i])

    return {
        "rows": [
            {"label": "Net Profit Margin", "values": npm, "format": "%"},
            {"label": "x  Total Asset Turnover", "values": tat, "format": "x"},
            {"label": "x  Equity Multiplier", "values": em, "format": "x"},
            {"label": "=  ROE (from DuPont)", "values": derived, "format": "%"},
            {"label": "ROE (from Profitability block)", "values": roe, "format": "%"},
        ],
        "check": check,
        "ok": all(c is None or abs(c) < 1e-9 for c in check),
    }


def market(data, extras, fys, price, shares):
    """
    EPS, P/E, Market/Book, market cap.

    EPS prefers the filer's own reported diluted figure. Failing that it divides
    Net Income by a weighted-average share count. Failing THAT it falls back to
    the cover-page count and says so via eps_basis -- dual-class filers tag EPS
    per share class, and companyfacts carries only undimensioned facts, so the
    reported figure is genuinely absent for some ordinary companies.

    Market cap and Market/Book use the cover-page count, which is current.
    Mixing that with a weighted average would be wrong in both directions.
    """
    n = len(fys)
    latest = n - 1
    eps_direct = extras.get("_eps", [None] * n)
    eps_src = extras.get("_eps_src", [None] * n)
    wavg = extras.get("_shares", [None] * n)
    wavg_src = extras.get("_shares_src", [None] * n)
    ni = data.get("Net Income", [None] * n)
    eq = data.get("Total Shareholders' Equity", [None] * n)

    eps, basis = [], []
    for i in range(n):
        if eps_direct[i] is not None:
            eps.append(eps_direct[i])
            basis.append("reported ({})".format(eps_src[i]))
        elif wavg[i] is not None:
            eps.append(_div(ni[i], wavg[i]))
            basis.append("Net Income / {}".format(wavg_src[i]))
        elif shares:
            eps.append(_div(ni[i], shares))
            basis.append("approximate - Net Income / cover-page shares")
        else:
            eps.append(None)
            basis.append(None)

    out = {
        "eps": eps,
        "eps_basis": basis[latest] if basis else None,
        "eps_approximate": bool(basis) and (basis[latest] or "").startswith("approximate"),
        "price": price,
        "shares_outstanding": shares,
        "market_cap": None,
        "pe": None,
        "pe_note": None,
        "book_value_per_share": None,
        "market_to_book": None,
        "available": False,
    }
    if price is None:
        return out

    e = eps[latest]
    if e is None:
        out["pe_note"] = "no EPS available"
    elif e <= 0:
        # A negative P/E is arithmetic, not information. Crocs lost money in
        # FY2025; the multiple says nothing about how it is valued.
        out["pe_note"] = "not meaningful - the company lost money in FY{}".format(fys[latest])
    else:
        out["pe"] = price / e

    if shares:
        out["market_cap"] = price * shares
        bvps = _div(eq[latest], shares)
        out["book_value_per_share"] = bvps
        if bvps and bvps > 0:
            out["market_to_book"] = price / bvps
    out["available"] = any(out[k] is not None
                           for k in ("pe", "market_to_book", "market_cap"))
    return out
