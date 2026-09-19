"""
Fill a workbook template with pulled data and hand back an .xlsx.

Only the Financial Data sheet gets written. In the full template, Common-Size,
Trend and Ratios are already wired with formulas pointing back at it, so they
recalculate on open -- the download is auditable, not a picture of a spreadsheet.

The data-only template is the same workbook with those formulas absent. Same
sheets, same labels, same row numbers; the analysis cells are empty for the
reader to build. Both come from one source of truth so a fix to the extraction
lands in both.
"""
import io
import os
import datetime
import openpyxl

_DATA = os.path.join(os.path.dirname(__file__), "..", "data")
TEMPLATES = {
    "full": os.path.join(_DATA, "template.xlsx"),
    "data": os.path.join(_DATA, "template_data_only.xlsx"),
}
TEMPLATE = TEMPLATES["full"]            # kept for callers that predate `mode`
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
FAT_ROW = 30                # blank spacer in the template, right after Total Asset Turnover
REVENUE_ROW = 10


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


def _add_fixed_asset_turnover(ws_ratios, nyears, with_formula=True):
    """
    Fixed Asset Turnover = Revenue / Gross PP&E, in the EFFICIENCY block.

    The label goes in either way. In data mode only the formula is withheld --
    otherwise the data-only workbook would list fourteen ratios where the full
    one lists fifteen, and the reader would never know a row was missing.
    """
    src = ws_ratios.cell(row=29, column=1)   # Total Asset Turnover
    c = ws_ratios.cell(row=FAT_ROW, column=1, value="Fixed Asset Turnover")
    c.font = src.font.copy()
    gross_row = MEMO_HEADER_ROW + 1
    for i, col in enumerate(YCOL[:nyears]):
        cell = ws_ratios[col + str(FAT_ROW)]
        model = ws_ratios[col + "29"]
        cell.number_format = model.number_format
        # Match the row above so the cell reads as one to fill in.
        cell.fill = model.fill.copy()
        cell.border = model.border.copy()
        if with_formula:
            cell.value = ("=IF('Financial Data'!{c}{g}=0,\"\","
                          "'Financial Data'!{c}{r}/'Financial Data'!{c}{g})").format(
                c=col, r=REVENUE_ROW, g=gross_row)


def build(result, scale=1e6, units="Millions USD", mode="full"):
    """
    result: the dict returned by pull.pull()
    mode:   "full" writes the solved workbook; "data" writes statements only,
            leaving every analysis cell empty.
    Returns (BytesIO, filename).
    """
    if mode not in TEMPLATES:
        raise ValueError("unknown mode: {}".format(mode))

    wb = openpyxl.load_workbook(TEMPLATES[mode])
    ws = wb["Financial Data"]
    _add_memo_rows(ws)
    _add_fixed_asset_turnover(wb["Ratios"], len(result["fiscal_years"]),
                              with_formula=(mode == "full"))
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

    _write_provenance(wb, result)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = "{} - {} - EDGAR {}{}.xlsx".format(
        result["ticker"], result["name"][:40].strip(),
        datetime.date.today().isoformat(),
        "" if mode == "full" else " (data only)")
    return buf, fname


def _write_provenance(wb, result):
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

    ws.column_dimensions["A"].width = 44
    for col in "BCDEFG":
        ws.column_dimensions[col].width = 30
