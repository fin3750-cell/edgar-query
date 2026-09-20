"""
Fill the workbook template with pulled data and hand back an .xlsx.

Only Financial Data and Sources get written. Common-Size, Trend and Ratios carry
their labels, headings and formatting but no formulas -- deriving those is the
point of handing someone the file, so the workbook never ships them worked.

There is deliberately no formula-filled variant. It existed briefly and went out
as a student copy by mistake; the safe design is for one not to exist.
"""
import io
import os
import datetime
import openpyxl

from . import ratios

_DATA = os.path.join(os.path.dirname(__file__), "..", "data")
TEMPLATE = os.path.join(_DATA, "template.xlsx")
YCOL = ["C", "D", "E", "F", "G"]        # the template holds five years

# Financial Data sheet: label -> row. Read from column A at load time so a
# template edit does not silently write to the wrong row.
HEADER_ROWS = {"Company name": 3, "Ticker symbol": 4, "Units": 5, "Data source": 6}


def _row_index(ws):
    idx = {}
    for r in range(1, ws.max_row + 1):
        v = ws.cell(row=r, column=1).value
        if isinstance(v, str) and v.strip():
            idx.setdefault(v.strip(), r)
    return idx


MEMO_HEADER_ROW = 43
MEMO_LABELS = ["Property, Plant & Equipment (gross)", "Accumulated Depreciation"]
REVENUE_ROW = 10

# Ratios sheet: the years occupy C..G, H is a 2-wide spacer and I holds the
# formula hints. The benchmark goes in H, widened -- to the right of the
# company's own years and to the left of the hint that says how to compute
# them, which is where a reader looks after filling a row in.
BENCH_COL = "H"
BENCH_HEADER_ROW = 8
BENCH_NOTE_ROWS = (4, 5)

# The market block starts below the DuPont footnote on row 40, for the reason
# _add_memo_rows gives: appending shifts nothing, and openpyxl will not rewrite
# the fixed row references that Common-Size, Trend and Ratios point at.
MARKET_ROW = 42

# Colours lifted from the template rather than invented, so the block reads as
# part of the same sheet: yellow means "this number is given", green means "you
# write the formula", and the two are what START HERE already explains.
FILL_GIVEN = "FFF2CC"
FILL_FORMULA = "E8F5E9"
FILL_HEADER = "DCE6F1"


def _add_memo_rows(ws):
    """
    Gross PP&E and accumulated depreciation, written BELOW the check row.

    They go at the bottom on purpose. Inserting them next to Net PP&E would
    shift every row beneath, and openpyxl does not rewrite the formulas on the
    Common-Size, Trend and Ratios sheets that point at fixed row numbers -- the
    whole workbook would silently reference the wrong lines.
    """
    src = ws.cell(row=26, column=1)          # Property, Plant & Equipment (net)
    ws.cell(row=MEMO_HEADER_ROW, column=1,
            value="MEMO  -  not part of the subtotals above")
    ws.cell(row=MEMO_HEADER_ROW, column=1).font = ws.cell(row=20, column=1).font.copy()
    for i, label in enumerate(MEMO_LABELS):
        r = MEMO_HEADER_ROW + 1 + i
        c = ws.cell(row=r, column=1, value=label)
        c.font = src.font.copy()
        for col in range(3, 8):
            ws.cell(row=r, column=col).number_format = \
                ws.cell(row=26, column=col).number_format



def _add_market_block(ws, result, quote, bench):
    """
    MARKET RATIOS, below the DuPont section.

    These are the ratios EDGAR alone cannot produce, and they are the reason
    the block has to look different from the four above it. Everything on this
    sheet so far divides one figure on Financial Data by another. P/E cannot:
    it needs a share price, which is not in a filing and not on that sheet.

    So the price and the share count arrive filled in, in the template's yellow
    -- givens, like the Financial Data sheet itself -- and the five rows under
    them stay green and empty. The unit trap is the interesting part and the
    hints say so out loud: Financial Data is in millions, so a share count in
    millions divides into it to give dollars per share, and market cap comes out
    in millions to match the statements beside it.
    """
    r = MARKET_ROW
    label_font = ws["A33"].font.copy()
    hint_font = ws["I10"].font.copy()
    model = ws["C33"]                       # a green DuPont cell: borders, look

    head = ws.cell(row=r, column=1,
                   value="MARKET RATIOS  -  what the market pays for what you just measured")
    head.font = ws["A31"].font.copy()
    head.fill = ws["A31"].fill.copy()
    r += 1

    note = ws.cell(row=r, column=1, value=(
        "Two numbers EDGAR does not publish are filled in for you. The rest are yours. "
        "Financial Data is in millions, so keep the share count in millions too."))
    note.font = ws["A40"].font.copy()
    r += 2

    hdr = ws.cell(row=r, column=1, value="As of")
    hdr.font = ws["C8"].font.copy()
    asof = ws.cell(row=r, column=3, value=_quote_date(quote)
                   or datetime.date.today().isoformat())
    asof.font = ws["C8"].font.copy()
    asof.fill = openpyxl.styles.PatternFill("solid", fgColor=FILL_HEADER)
    market_bench = (bench or {}).get("market") or {}
    if market_bench.get("available"):
        ind = ws.cell(row=r, column=8, value="Industry")
        ind.font = ws["C8"].font.copy()
        ind.fill = openpyxl.styles.PatternFill("solid", fgColor=FILL_HEADER)
        # H is a 2-wide spacer in the stock template. _write_benchmark widens it
        # when there are medians to show, but a bank has no medians and still
        # gets a P/E -- the multiples do not depend on the revenue rows that
        # made it unsuitable -- so the width has to be set here too.
        ws.column_dimensions[BENCH_COL].width = max(
            ws.column_dimensions[BENCH_COL].width or 0, 15)
    r += 1

    price = (quote or {}).get("price")
    shares = result.get("shares_outstanding")

    # label, kind, value, number format, hint.
    #
    # "given" and "formula" are decided by the row, never by whether a value
    # turned up. A failed price lookup leaves the price row yellow and empty
    # with the reason beside it; colouring it green instead would tell the
    # reader to go and derive a share price, which is not a thing they can do
    # from this workbook.
    rows = [
        ("Share price (USD)", "given", price, "#,##0.00",
         _price_note(quote)),
        ("Shares outstanding (millions)", "given",
         None if not shares else round(shares / 1e6, 1), "#,##0.0",
         result.get("shares_source") if shares
         else "not resolved - fill this in yourself"),
        ("Earnings per Share (EPS)", "formula", None, "#,##0.00",
         "Net Income / Shares outstanding  -  millions over millions gives dollars"),
        ("Book Value per Share", "formula", None, "#,##0.00",
         "Total Shareholders' Equity / Shares outstanding"),
        ("Market Capitalisation (millions)", "formula", None, "#,##0.0",
         "Share price x Shares outstanding"),
        ("Price / Earnings (P/E)", "formula", None, "0.00",
         "Share price / Earnings per Share"),
        ("Market / Book (P/B)", "formula", None, "0.00",
         "Share price / Book Value per Share"),
    ]

    for label, kind, value, fmt, hint in rows:
        ws.cell(row=r, column=1, value=label).font = label_font
        cell = ws.cell(row=r, column=3)
        cell.number_format = fmt
        cell.border = model.border.copy()
        cell.fill = openpyxl.styles.PatternFill(
            "solid", fgColor=FILL_GIVEN if kind == "given" else FILL_FORMULA)
        if value is not None:
            cell.value = value
        h = ws.cell(row=r, column=9,
                    value=("Given - " if kind == "given" else "") + hint)
        h.font = hint_font

        entry = market_bench.get("ratios", {}).get(label)
        if entry is not None:
            b = ws.cell(row=r, column=8, value=round(entry, 2))
            b.number_format = fmt
            b.border = model.border.copy()
        r += 1

    r += 1
    tail = ws.cell(row=r, column=1, value=(
        "A P/E on a company that lost money is arithmetic, not information. If your EPS "
        "is negative, say so rather than printing the number."))
    tail.font = ws["A40"].font.copy()
    r += 1

    if market_bench.get("available"):
        src = ws.cell(row=r, column=1, value=(
            "Industry column: {} for {}, {}. These two are aggregates -- industry "
            "market value over industry earnings and book value -- not medians like "
            "column H above, and they come from a different source. See Sources."
            .format(market_bench.get("source"), market_bench.get("industry"),
                    market_bench.get("updated"))))
        src.font = ws["A40"].font.copy()
    return r


def _quote_date(quote):
    """market.quote() carries Yahoo's raw epoch seconds; the sheet wants a date."""
    ts = (quote or {}).get("as_of")
    if not ts:
        return None
    try:
        return datetime.datetime.fromtimestamp(int(ts)).date().isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _price_note(quote):
    """One phrase saying where the price came from, or why there isn't one."""
    if not quote or quote.get("price") is None:
        return "no price available ({})".format(
            (quote or {}).get("error") or "lookup not attempted")
    bits = [quote.get("source") or "market data"]
    if quote.get("exchange"):
        bits.append(quote["exchange"])
    if _quote_date(quote):
        bits.append(_quote_date(quote))
    return ", ".join(bits)


def _write_benchmark(ws, bench):
    """
    The industry median column, and a two-line caption saying what it is.

    The caption is not decoration. A median is only as good as the number of
    filers under it and how wide a net was cast to find them, and both vary a
    lot by industry -- so the level, the count and the vintage go on the face of
    the sheet rather than being left for someone to go looking for.

    When there is no benchmark the caption still gets written, saying why. A
    column that is silently absent reads as a bug.
    """
    note_a, note_b = BENCH_NOTE_ROWS
    hint_font = ws["A2"].font.copy()

    if not bench or not bench.get("available"):
        ws["A" + str(note_a)] = "Industry benchmark: not available for this company."
        ws["A" + str(note_a)].font = hint_font
        if bench and bench.get("note"):
            ws["A" + str(note_b)] = bench["note"]
            ws["A" + str(note_b)].font = hint_font
        return

    ws["A" + str(note_a)] = "Industry benchmark  -  {} ({} {}), median of {} 10-K filers".format(
        bench["name"], bench["level"], bench["code"], bench["filers"])
    ws["A" + str(note_a)].font = hint_font
    ws["A" + str(note_b)] = (
        "Column {} below. SEC Financial Statement Data Sets {}, built {}. Same XBRL tags "
        "and the same formulas as your own columns, so the two are comparable."
        .format(BENCH_COL, "/".join(bench.get("quarters") or []), bench.get("built") or "?"))
    ws["A" + str(note_b)].font = hint_font

    head = ws[BENCH_COL + str(BENCH_HEADER_ROW)]
    head.value = "Industry median"
    head.font = ws["C" + str(BENCH_HEADER_ROW)].font.copy()
    head.alignment = ws["C" + str(BENCH_HEADER_ROW)].alignment.copy()

    rows = _row_index(ws)
    for label, entry in bench["ratios"].items():
        r = rows.get(label)
        if not r or entry.get("median") is None:
            continue
        model = ws["C" + str(r)]
        cell = ws[BENCH_COL + str(r)]
        cell.value = round(entry["median"], 4)
        cell.number_format = model.number_format
        cell.border = model.border.copy()
        # Deliberately NOT the model's fill. The green on C..G means "you fill
        # this in"; this column is already filled, and should not invite edits.

    ws.column_dimensions[BENCH_COL].width = 15


def build(result, scale=1e6, units="Millions USD", bench=None, quote=None):
    """
    result: the dict returned by pull.pull()
    bench:  industry.benchmark(cik), or None to omit the benchmark column
    quote:  market.quote(ticker), or None -- the price row then says so
    Returns (BytesIO, filename).
    """
    wb = openpyxl.load_workbook(TEMPLATE)
    ws = wb["Financial Data"]
    _add_memo_rows(ws)
    _write_benchmark(wb["Ratios"], bench)
    _add_market_block(wb["Ratios"], result, quote, bench)
    rows = _row_index(ws)

    fys = result["fiscal_years"]
    ws["C3"] = result["name"]
    ws["C4"] = result["ticker"]
    ws["C5"] = units
    ws["C6"] = "SEC EDGAR XBRL API - https://data.sec.gov/api/xbrl/companyfacts/CIK{}.json".format(
        result["cik"])

    yr = rows.get("Fiscal Year")
    if yr:
        for i, col in enumerate(YCOL[:len(fys)]):
            ws[col + str(yr)] = "FY{}".format(fys[i])

    for label, values in result["data"].items():
        r = rows.get(label)
        if not r:
            continue
        for i, col in enumerate(YCOL[:len(values)]):
            v = values[i]
            if v is not None:
                ws[col + str(r)] = round(v / scale, 1)

    _write_provenance(wb, result, bench)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = "{} - {} - EDGAR {}.xlsx".format(
        result["ticker"], result["name"][:40].strip(),
        datetime.date.today().isoformat())
    return buf, fname


def _write_provenance(wb, result, bench=None):
    """
    A Sources tab naming the XBRL tag behind every year of every row.

    Without this the workbook is unfalsifiable -- you cannot tell a real figure
    from a tag-mapping accident. With it, any number can be traced back to the
    filing that produced it.
    """
    ws = wb.create_sheet("Sources")
    fys = result["fiscal_years"]

    ws["A1"] = "Where every number came from"
    ws["A2"] = ("One XBRL tag per year per row. A row can draw on more than one tag when "
                "the company changed presentation mid-window -- that is expected, not an error.")
    ws["A4"] = "Company"
    ws["B4"] = "{} ({})".format(result["name"], result["ticker"])
    ws["A5"] = "CIK"
    ws["B5"] = result["cik"]
    ws["A6"] = "Retrieved"
    ws["B6"] = datetime.datetime.now().isoformat(timespec="seconds")

    ends = result.get("period_ends") or [None] * len(fys)
    ws["A8"] = "Row"
    for i, y in enumerate(fys):
        # Jan/Feb year-ends are named inconsistently across filers -- Target
        # calls its Feb-2026 close "fiscal 2025", NVDA calls its Jan-2026 close
        # "fiscal 2026". The period end date is the only unambiguous label.
        ws.cell(row=8, column=2 + i,
                value="FY{}{}".format(y, "  (ended {})".format(ends[i]) if ends[i] else ""))

    r = 9
    for label, prov in result["provenance"].items():
        ws.cell(row=r, column=1, value=label)
        for i, tag in enumerate(prov):
            ws.cell(row=r, column=2 + i, value=tag or "not reported")
        r += 1

    diag = result["diagnostics"]
    r += 1
    ws.cell(row=r, column=1, value="Balance check (Assets - Liabilities - Equity)")
    r += 1
    for b in diag["balance"]:
        ws.cell(row=r, column=1, value="FY{}".format(b["year"]))
        ws.cell(row=r, column=2,
                value=b["status"] if b["diff"] is None else "{} ({:,.0f})".format(
                    b["status"], b["diff"]))
        r += 1

    if result.get("mezzanine_note"):
        r += 1
        ws.cell(row=r, column=1, value="Adjustment")
        ws.cell(row=r, column=2, value=result["mezzanine_note"])

    r = _write_benchmark_sources(ws, r + 2, bench)

    ws.column_dimensions["A"].width = 44
    for col in "BCDEFG":
        ws.column_dimensions[col].width = 30


def _write_benchmark_sources(ws, r, bench):
    """
    Where the industry column came from, and how thin it is per ratio.

    The per-ratio count is the part worth printing. A SIC with forty filers
    behind it can still have eight reporting an inventory, and Inventory
    Turnover for that industry is then a median of eight -- which is a different
    claim from the forty at the top of the block.
    """
    ws.cell(row=r, column=1, value="Industry benchmark")
    if not bench or not bench.get("available"):
        ws.cell(row=r, column=2,
                value=(bench or {}).get("note") or "not available for this company")
        return r + 1
    r += 1

    for label, value in (
            ("Industry", "{} ({} {})".format(bench["name"], bench["level"], bench["code"])),
            ("SIC reported by SEC", "{}{}".format(
                bench["sic"],
                "  -  " + bench["sic_description"] if bench.get("sic_description") else "")),
            ("Filers in the median", bench["filers"]),
            ("Source", "SEC Financial Statement Data Sets, {}".format(
                ", ".join(bench.get("quarters") or []) or "unknown quarters")),
            ("Table built", bench.get("built") or "unknown"),
            ("Method", "Median across filers, same XBRL tags and same formulas as "
                       "the columns above. One 10-K per filer."),
    ):
        ws.cell(row=r, column=1, value=label)
        ws.cell(row=r, column=2, value=value)
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="Ratio")
    ws.cell(row=r, column=2, value="Industry median")
    ws.cell(row=r, column=3, value="Filers reporting it")
    r += 1
    for label, _group, _n, _d, _fmt in ratios.SPEC:
        entry = bench["ratios"].get(label)
        ws.cell(row=r, column=1, value=label)
        if entry:
            ws.cell(row=r, column=2, value=round(entry["median"], 4)
                    if entry.get("median") is not None else None)
            ws.cell(row=r, column=3, value=entry.get("n"))
        else:
            # Named explicitly rather than left out. A blank cell in column H
            # reads as a bug; "too few filers report the inputs" reads as a
            # fact about the data, which is what it is. Fixed Asset Turnover
            # lands here for most industries -- barely any filer tags gross
            # PP&E, and only some tag the accumulated depreciation it is
            # derived from.
            ws.cell(row=r, column=2, value="no benchmark")
            ws.cell(row=r, column=3, value="too few filers report the inputs")
        r += 1

    return _write_market_sources(ws, r + 1, bench)


def _write_market_sources(ws, r, bench):
    """
    The market block's industry column, which is somebody else's data.

    It gets its own heading rather than being folded into the table above,
    because it is not the same kind of number: an aggregate, from a different
    author, over a different universe, rebuilt once a year rather than
    quarterly. Anyone comparing the two should be able to see that without
    having to know it already.
    """
    mkt = (bench or {}).get("market") or {}
    if not mkt.get("available"):
        return r

    ws.cell(row=r, column=1, value="Industry P/E and P/B")
    r += 1
    for label, value in (
            ("Source", "{} - {}".format(mkt.get("source"), mkt.get("source_url"))),
            ("Their industry group", "{}{}".format(
                mkt.get("industry"),
                "  ({} firms)".format(mkt["firms"]) if mkt.get("firms") else "")),
            ("Data updated", mkt.get("updated") or "undated"),
            ("Method", mkt.get("method") or ""),
            ("Not comparable with", "the medians above -- different source, "
                                    "different universe, aggregates not medians"),
    ):
        ws.cell(row=r, column=1, value=label)
        ws.cell(row=r, column=2, value=value)
        r += 1

    for label, value in (mkt.get("ratios") or {}).items():
        ws.cell(row=r, column=1, value=label)
        ws.cell(row=r, column=2, value=round(value, 2))
        r += 1
    return r
