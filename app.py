"""
edgar-query - standardized financial statements straight from SEC EDGAR.

    uvicorn app:app --reload

Set SEC_CONTACT in the environment. SEC's fair-access policy wants a real name
and email in the User-Agent; requests without one get throttled or blocked.
"""
import os
import json
import time
import hashlib
import logging
import threading

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from edgar import pull as pull_mod
from edgar import market, ratios, workbook, industry

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
    # A missing template is a packaging mistake, not a runtime one -- it would
    # only show up as a 502 on the first download, which is far too late. A
    # template was once dropped by a .gitignore rule exactly this way.
    template_ok = os.path.exists(workbook.TEMPLATE)
    # The industry table is reported but does not gate `ok`. Losing it degrades
    # one column of one sheet and fails soft; losing the template breaks every
    # download. Same reasoning as the template check, though -- a data file
    # dropped from the deploy is otherwise invisible until someone notices a
    # column that quietly stopped appearing.
    tbl = industry.table()
    return {"ok": template_ok,
            "sec_contact_configured": configured,
            "template": template_ok,
            "industry": {"loaded": tbl is not None,
                         "built": (tbl or {}).get("built"),
                         "codes": len((tbl or {}).get("industries", {}))},
            "asset_version": asset_version(),
            "cache": pull_mod.cache_stats()}


@app.get("/api/companies")
def companies():
    """The pre-screened list, grouped by sector. Any ticker still works."""
    try:
        with open(APPROVED, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


MODES = ("full", "data")


@app.get("/api/company/{ticker}")
def company(ticker: str, years: int = Query(5, ge=2, le=5),
            mode: str = Query("full", pattern="^(full|data)$")):
    """
    mode=full  statements, ratios, DuPont and market multiples.
    mode=data  the reported statements and nothing computed from them --
               no ratios, no DuPont, no multiples. Price and shares outstanding
               still come through, since those are inputs, not results.
    """
    result = _pull(ticker, years)

    payload = {
        "mode": mode,
        "ticker": result["ticker"],
        "name": result["name"],
        "cik": result["cik"],
        "fiscal_years": result["fiscal_years"],
        "period_ends": result["period_ends"],
        "statements": result["data"],
        "provenance": result["provenance"],
        "shares_asof": result["shares_asof"],
        "mezzanine_note": result["mezzanine_note"],
        "diagnostics": result["diagnostics"],
    }

    q = market.quote(result["ticker"])
    payload["quote"] = q

    if mode == "data":
        payload["shares_outstanding"] = result["shares_outstanding"]
        return payload

    sp = market.splits(result["ticker"])
    rows = ratios.compute(result["data"], result["fiscal_years"])
    mkt = ratios.market(result["data"], result["extras"], result["fiscal_years"],
                        q["price"], result["shares_outstanding"],
                        period_ends=result["period_ends"],
                        split_events=sp["splits"])
    mkt["split_lookup_ok"] = sp["ok"]

    payload["ratios"] = rows
    payload["dupont"] = ratios.dupont(rows, result["fiscal_years"])
    payload["market"] = mkt
    return payload


@app.get("/api/company/{ticker}/xlsx")
def company_xlsx(ticker: str, years: int = Query(5, ge=2, le=5)):
    """
    Always the template: statements filled, analysis sheets labelled and empty.

    There is no worked variant and no mode switch here on purpose -- a URL that
    could be edited to hand back the answers is a URL someone will edit.

    The industry column is the one exception to "analysis sheets are empty", and
    it is not an answer -- it is a given, like the share price. Knowing the
    industry's median current ratio tells you nothing about this company's until
    you have worked yours out.
    """
    result = _pull(ticker, years)
    buf, fname = workbook.build(result, bench=industry.benchmark(result["cik"]))
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


ASSET_FILES = ("index.html", "app.js", "style.css")
_version_cache = {"key": None, "value": "0"}
_version_lock = threading.Lock()


def asset_version():
    """
    Short hash of the frontend files, keyed on their mtime and size.

    Without a version stamp a deploy does not reliably reach a browser holding
    the old bundle: StaticFiles sends an ETag but no Cache-Control, so a client
    may reuse app.js without revalidating. That is how a stale frontend keeps
    rendering ratios after a mode that removes them has shipped.

    Computing it once at import would be enough in production, where a deploy is
    a new process -- but it makes local editing actively worse, because the stamp
    never changes while the browser has been told that stamp is immutable. The
    stat() is cheap; correctness in both places is worth it.
    """
    key = []
    for name in ASSET_FILES:
        try:
            st = os.stat(os.path.join(STATIC, name))
            key.append((name, st.st_mtime_ns, st.st_size))
        except OSError:
            key.append((name, 0, 0))
    key = tuple(key)

    with _version_lock:
        if _version_cache["key"] == key:
            return _version_cache["value"]

    h = hashlib.sha256()
    for name in ASSET_FILES:
        try:
            with open(os.path.join(STATIC, name), "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(name.encode())
    value = h.hexdigest()[:10]

    with _version_lock:
        _version_cache["key"] = key
        _version_cache["value"] = value
    return value


class VersionedStatic(StaticFiles):
    """Immutable when the URL carries ?v=, always revalidate when it does not."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        query = scope.get("query_string", b"").decode()
        if "v=" in query:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


@app.get("/")
@app.head("/")
def index():
    """
    index.html is rewritten in flight to stamp the asset version onto the script
    and stylesheet URLs, and is itself sent no-store so the stamp is never stale.
    """
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    v = asset_version()
    html = (html
            .replace("/static/app.js", "/static/app.js?v=" + v)
            .replace("/static/style.css", "/static/style.css?v=" + v))
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.exception_handler(404)
def not_found(request, exc):
    return JSONResponse({"detail": getattr(exc, "detail", "Not found")}, status_code=404)


app.mount("/static", VersionedStatic(directory=STATIC), name="static")
