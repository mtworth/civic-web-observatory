import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
from dotenv import load_dotenv

load_dotenv()
from fastapi import FastAPI, Query, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates

STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["format_int"] = lambda v: f"{int(v):,}" if v is not None else "0"

# Simple TTL cache for aggregate stats endpoints. Stats only change when a
# crawl finishes, so 60s staleness is fine and avoids repeated MotherDuck
# round-trips on every page load.
_CACHE_TTL = 60  # seconds
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, fn):
    now = time.monotonic()
    if key in _cache:
        ts, val = _cache[key]
        if now - ts < _CACHE_TTL:
            return val
    val = fn()
    _cache[key] = (now, val)
    return val


def _queries():
    """Lazy import so the module loads cleanly before pwo/queries.py exists."""
    from . import queries  # noqa: PLC0415
    return queries

def _db_path() -> str:
    """Return MotherDuck connection string if token is set, else local file path."""
    token = os.getenv("MOTHERDUCK_TOKEN")
    if token:
        return f"md:pwo?motherduck_token={token}"
    return os.getenv("PWO_DB_PATH", "outputs/observations.duckdb")


def get_db():
    """Open a fresh DuckDB connection per request.

    FastAPI runs sync handlers in a thread pool, so concurrent requests race
    on any shared connection object. Per-request connections are safe because
    DuckDB supports multiple simultaneous read-only openers (local file) and
    MotherDuck handles concurrent reads natively.

    After the first connect() call, DuckDB's MotherDuck extension keeps an
    internal process-level session, so subsequent opens cost ~0ms. The
    background refresh loop below establishes that session at startup.
    """
    path = _db_path()
    read_only = not path.startswith("md:")
    conn = duckdb.connect(path, read_only=read_only)
    try:
        yield conn
    finally:
        conn.close()


def _connect_and_prepare() -> duckdb.DuckDBPyConnection:
    from .db import VIEWS  # noqa: PLC0415
    conn = duckdb.connect(_db_path())
    for view_sql in VIEWS:
        try:
            conn.execute(view_sql)
        except Exception:
            pass
    return conn


def _refresh_once(conn: duckdb.DuckDBPyConnection) -> None:
    now = time.monotonic()
    _cache["all_stats"] = (now, _queries().get_dashboard_stats(conn))
    _cache["summary"] = (now, _queries().get_run_summary(conn))
    _cache["recent"] = (now, _queries().get_recent_observations(conn))


async def _refresh_loop():
    """Proactively keep the home-page cache entries warm.

    get_dashboard_stats issues ~8 separate queries against MotherDuck and
    costs ~1-1.5s end to end (MotherDuck per-query overhead, not raw network
    RTT — a trivial `SELECT 1` returns in ~1ms). Refreshing here, off the
    request path, means page loads always hit the in-memory cache instead of
    occasionally blocking on a cold query right after TTL expiry.
    """
    conn = None
    while True:
        try:
            if conn is None:
                conn = await asyncio.to_thread(_connect_and_prepare)
            await asyncio.to_thread(_refresh_once, conn)
        except Exception:
            conn = None  # force reconnect + view recreation next iteration
        await asyncio.sleep(20)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Scheduled (not awaited) so the app starts accepting requests
    # (including Railway's healthcheck on /api/health) immediately.
    task = asyncio.create_task(_refresh_loop())
    yield
    task.cancel()


app = FastAPI(title="Civic Web Index", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/")
def root():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"status": "ok", "version": "0.1.0"}


@app.get("/domains/{domain}", response_class=HTMLResponse)
def domain_page(request: Request, domain: str, conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    result = _queries().get_domain(conn, domain)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Domain '{domain}' not found")
    checked_at = result.get("checked_at") or ""
    if checked_at:
        try:
            dt = datetime.fromisoformat(checked_at)
            checked_at = dt.strftime("%-d %B %Y")
        except Exception:
            pass
    return templates.TemplateResponse(request=request, name="domain.html", context={"d": result, "checked_at": checked_at})


@app.get("/insights", response_class=HTMLResponse)
def insights_page(request: Request, conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    stats = _cached("all_stats", lambda: _queries().get_dashboard_stats(conn))
    total = stats.get("total", 0)
    status = stats.get("status", [])
    blocked_statuses = {"captcha", "bot_challenge", "blocked_by_cdn", "http_403", "http_429"}
    ok_count = sum(s["count"] for s in status if s["status"] == "ok")
    blocked_count = sum(s["count"] for s in status if s["status"] in blocked_statuses)
    return templates.TemplateResponse(request=request, name="insights.html", context={
        "total": total,
        "ok_count": ok_count,
        "blocked_count": blocked_count,
        "https": stats.get("https", {}),
        "tls": stats.get("tls", {}),
        "files": stats.get("files", {}),
        "a11y": stats.get("a11y", {}),
        "status": status,
        "hosting": stats.get("hosting", []),
        "blocking": stats.get("blocking", []),
        "technologies": stats.get("technologies", []),
    })


@app.get("/llms.txt")
def llms_txt():
    path = STATIC_DIR / "llms.txt"
    if path.exists():
        return FileResponse(path, media_type="text/plain")
    raise HTTPException(status_code=404)


@app.get("/robots.txt")
def robots_txt():
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "\n"
        "User-agent: CivicWebIndexBot\n"
        "Allow: /\n"
        "\n"
        "Sitemap: https://civicwebindex.org/sitemap.xml\n"
    )
    return Response(content=content, media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    base = "https://civicwebindex.org"
    rows = conn.execute("SELECT domain FROM v_current ORDER BY domain").fetchall()
    urls = [
        f"  <url><loc>{base}/</loc><changefreq>daily</changefreq></url>",
        f"  <url><loc>{base}/insights</loc><changefreq>daily</changefreq></url>",
    ] + [
        f"  <url><loc>{base}/domains/{row[0]}</loc><changefreq>monthly</changefreq></url>"
        for row in rows
    ]
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += "\n".join(urls) + "\n"
    xml += "</urlset>"
    return Response(content=xml, media_type="application/xml")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/api/stats")
def get_all_stats(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Single endpoint returning everything the dashboard needs in one round-trip."""
    return _cached("all_stats", lambda: _queries().get_dashboard_stats(conn))


@app.get("/api/recent")
def get_recent(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Random sample of observations for the home page feed. Short cache so it rotates."""
    now = time.monotonic()
    key = "recent"
    if key in _cache:
        ts, val = _cache[key]
        if now - ts < 30:   # 30s — shorter than stats so the feed feels fresh
            return val
    val = _queries().get_recent_observations(conn)
    _cache[key] = (now, val)
    return val


@app.get("/api/summary")
def get_summary(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _cached("summary", lambda: _queries().get_run_summary(conn))


@app.get("/api/stats/status")
def get_status_breakdown(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _cached("status", lambda: _queries().get_collection_status_breakdown(conn))


@app.get("/api/stats/https")
def get_https_adoption(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _cached("https", lambda: _queries().get_https_adoption(conn))


@app.get("/api/stats/hosting")
def get_hosting_breakdown(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _cached("hosting", lambda: _queries().get_hosting_breakdown(conn))


@app.get("/api/stats/tls")
def get_tls_stats(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _cached("tls", lambda: _queries().get_tls_stats(conn))


@app.get("/api/stats/files")
def get_file_availability(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _cached("files", lambda: _queries().get_file_availability(conn))


@app.get("/api/stats/accessibility")
def get_accessibility_summary(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _cached("accessibility", lambda: _queries().get_accessibility_summary(conn))


@app.get("/api/stats/blocking")
def get_blocking_breakdown(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _cached("blocking", lambda: _queries().get_block_type_breakdown(conn))


@app.get("/api/stats/technologies")
def get_tech_breakdown(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _cached("technologies", lambda: _queries().get_tech_breakdown(conn))


@app.get("/api/domains")
def list_domains(
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    sector: str | None = Query(default=None),
    org_type: str | None = Query(default=None),
    state: str | None = Query(default=None),
    search: str | None = Query(default=None),
):
    return _queries().list_domains(
        conn, limit=limit, offset=offset, status=status,
        sector=sector, org_type=org_type, state=state, search=search,
    )


@app.get("/api/stats/states")
def get_state_breakdown(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _queries().get_state_breakdown(conn)


@app.get("/api/stats/org-types")
def get_org_type_breakdown(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _queries().get_org_type_breakdown(conn)


@app.get("/api/domains/{domain}/history")
def get_domain_history(domain: str, conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _queries().get_domain_history(conn, domain)


@app.get("/api/domains/{domain}")
def get_domain(domain: str, conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    result = _queries().get_domain(conn, domain)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Domain '{domain}' not found")
    return result


@app.get("/api/blocked")
def get_blocked(
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    return _queries().get_blocked_domains(conn, limit=limit, offset=offset)


@app.get("/api/runs")
def get_runs(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _queries().get_run_summary(conn)
