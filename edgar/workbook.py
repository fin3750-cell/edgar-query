"""
Fill the Module 05 template with pulled data and hand back an .xlsx.

Only the Financial Data sheet gets written. Common-Size, Trend and Ratios are
already wired with formulas that point back at it, so they recalculate on open.
That is deliberate: the download is auditable, not a picture of a spreadsheet.
"""
import io
import os
import datetime
import openpyxl

TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "data", "template.xlsx")
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


def build(result, scale=1e6, units="Millions USD"):
    """
    result: the dict returned by pull.pull()
    Returns (BytesIO, filename).
    """
    wb = openpyxl.load_workbook(TEMPLATE)
    ws = wb["Financial Data"]
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
    fname = "{} - {} - EDGAR {}.xlsx".format(
        result["ticker"], result["name"][:40].strip(),
        datetime.date.today().isoformat())
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

    ws["A8"] = "Row"
    for i, y in enumerate(fys):
        ws.cell(row=8, column=2 + i, value="FY{}".format(y))

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
