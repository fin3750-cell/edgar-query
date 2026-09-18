"""
edgar-query - standardized financial statements straight from SEC EDGAR.

    uvicorn app:app --reload

Set SEC_CONTACT in the environment. SEC's fair-access policy wants a real name
and email in the User-Agent; requests without one get throttled or blocked.
"""
import os
import json
import time
import logging
import threading

from fastapi import FastAPI, HTTPException, Query, Request
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

# Requests per minute per client IP, on /api/ only. Generous on purpose: a whole
# class often sits behind one campus NAT address, so the limit has to clear a
# room full of people rather than one person. Cached lookups cost nothing, and
# after the first pull of a ticker everyone else is served from memory -- so this
# is a brake on abuse, not on normal use. Raise it with RATE_LIMIT_PER_MIN, or
# set 0 to switch it off.
RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_MIN", "240"))
_buckets = {}
_bucket_lock = threading.Lock()


def _client_ip(request):
    """
    Render terminates TLS at a proxy, so request.client.host is the proxy. The
    real caller is first in X-Forwarded-For.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    # Static files and /healthz stay open: the health check must answer even
    # when a caller has spent its budget, or Render reads a 429 as a dead app.
    if RATE_LIMIT <= 0 or not request.url.path.startswith("/api/"):
        return await call_next(request)

    now = time.time()
    window = int(now // 60)
    key = (_client_ip(request), window)

    with _bucket_lock:
        if len(_buckets) > 4096:            # prune windows that have rolled over
            for k in [k for k in _buckets if k[1] < window]:
                del _buckets[k]
        count = _buckets.get(key, 0) + 1
        _buckets[key] = count

    if count > RATE_LIMIT:
        retry = 60 - int(now % 60)
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry)},
            content={"detail": "Rate limit of {}/minute exceeded. Try again in {}s."
                               .format(RATE_LIMIT, retry)})

    return await call_next(request)


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
