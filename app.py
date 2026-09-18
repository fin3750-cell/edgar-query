"""
edgar-query - standardized financial statements straight from SEC EDGAR.

    uvicorn app:app --reload

Set SEC_CONTACT in the environment. SEC's fair-access policy wants a real name
and email in the User-Agent; requests without one get throttled or blocked.
"""
import os
import json
import logging

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from edgar import pull as pull_mod
from edgar import market, ratios, workbook

log = logging.getLogger("uvicorn.error")

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
APPROVED = os.path.join(HERE, "data", "approved_companies.json")

app = FastAPI(title="edgar-query", docs_url="/api/docs")

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@app.get("/healthz")
def healthz():
    configured = not pull_mod.CONTACT.startswith("edgar-query educational tool")
    return {"ok": True, "sec_contact_configured": configured,
            "cache": pull_mod.cache_stats()}


@app.get("/api/companies")
def companies():
    """The pre-screened list, grouped by sector. Any ticker still works."""
    try:
        with open(APPROVED, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


@app.get("/api/company/{ticker}")
def company(ticker: str, years: int = Query(5, ge=2, le=5)):
    result = _pull(ticker, years)

    rows = ratios.compute(result["data"], result["fiscal_years"])
    q = market.quote(result["ticker"])
    sp = market.splits(result["ticker"])
    mkt = ratios.market(result["data"], result["extras"], result["fiscal_years"],
                        q["price"], result["shares_outstanding"],
                        period_ends=result["period_ends"],
                        split_events=sp["splits"])
    mkt["split_lookup_ok"] = sp["ok"]

    return {
        "ticker": result["ticker"],
        "name": result["name"],
        "cik": result["cik"],
        "fiscal_years": result["fiscal_years"],
        "period_ends": result["period_ends"],
        "statements": result["data"],
        "provenance": result["provenance"],
        "ratios": rows,
        "dupont": ratios.dupont(rows, result["fiscal_years"]),
        "market": mkt,
        "quote": q,
        "shares_asof": result["shares_asof"],
        "mezzanine_note": result["mezzanine_note"],
        "diagnostics": result["diagnostics"],
    }


@app.get("/api/company/{ticker}/xlsx")
def company_xlsx(ticker: str, years: int = Query(5, ge=2, le=5)):
    result = _pull(ticker, years)
    buf, fname = workbook.build(result)
    return StreamingResponse(
        buf, media_type=XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="{}"'.format(fname)})


def _pull(ticker, years):
    if not ticker.replace("-", "").replace(".", "").isalnum() or len(ticker) > 10:
        raise HTTPException(400, "That does not look like a ticker symbol.")
    try:
        return pull_mod.pull(ticker, years)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:                        # noqa: BLE001
        log.exception("pull failed for %s", ticker)
        raise HTTPException(502, "EDGAR request failed: {}".format(exc))


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.exception_handler(404)
def not_found(request, exc):
    return JSONResponse({"detail": getattr(exc, "detail", "Not found")}, status_code=404)


app.mount("/static", StaticFiles(directory=STATIC), name="static")
