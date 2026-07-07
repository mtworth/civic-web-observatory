import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, quote

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
templates.env.filters["urlquote"] = lambda v: quote(str(v), safe="")

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
    internal process-level session, so subsequent opens cost ~0ms. Startup
    (see lifespan below) makes that first call so the very first real
    request doesn't pay for it.
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """One-time startup: create views/migrations and warm the MotherDuck session.

    Deliberately not a recurring background loop — the `_cached()` helper
    already lazily recomputes on the first request after each entry's 60s
    TTL expires, so query cost tracks actual visitor traffic instead of a
    fixed background poll running 24/7 regardless of whether anyone's
    visiting (a prior version of this polled MotherDuck every 20s forever,
    which is a lot of billed compute for zero incremental freshness benefit).
    """
    try:
        conn = await asyncio.to_thread(_connect_and_prepare)
        conn.close()
    except Exception:
        pass
    yield


app = FastAPI(title="Civic Web Index", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "unknown"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return "unknown"
    return dt.strftime("%B %-d, %Y") if os.name != "nt" else dt.strftime("%B %#d, %Y")


def _footer_note(summary: dict) -> str | None:
    checked = summary.get("dataset_last_checked_at")
    return f"Last observation {_fmt_date(checked)}" if checked else None


_BLOCKED_STATUSES = {"captcha", "bot_challenge", "blocked_by_cdn", "http_403", "http_429"}


def _signal_for(d: dict) -> tuple[str, str]:
    block_type = d.get("homepage_block_type")
    if block_type and block_type != "none":
        return f"blocked · {block_type.replace('_', ' ')}", "warn"
    expiry = d.get("tls_days_until_expiry")
    if expiry is not None and expiry < 30:
        return f"TLS expires in {expiry}d", "warn"
    if d.get("llms_txt_available"):
        return "serves llms.txt", "ok"
    technologies = d.get("technologies") or []
    if technologies:
        return " · ".join(technologies[:2]), ""
    if d.get("dns_resolves") is False:
        return "DNS not resolving", "fail"
    if d.get("https_available") and d.get("tls_valid"):
        return "HTTPS · valid TLS", "ok"
    if d.get("https_available") and not d.get("tls_valid"):
        return "HTTPS · invalid TLS", "warn"
    return d.get("collection_status") or "checked", ""


def _time_ago(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = int((datetime.now(timezone.utc) - dt).total_seconds())
    if diff < 60:
        return f"{diff}s ago"
    if diff < 3600:
        return f"{diff // 60}m ago"
    if diff < 86400:
        return f"{diff // 3600}h ago"
    return f"{diff // 86400}d ago"


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/", response_class=HTMLResponse)
def root(request: Request, conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    summary = _cached("summary", lambda: _queries().get_run_summary(conn))
    recent = _cached("recent", lambda: _queries().get_recent_observations(conn))
    for d in recent:
        d["signal_text"], d["signal_cls"] = _signal_for(d)
        d["time_ago"] = _time_ago(d.get("checked_at"))
    return templates.TemplateResponse(request=request, name="home.html", context={
        "active": "home",
        "total": summary.get("dataset_domains_total", 0),
        "recent": recent[:8],
        "footer_note": _footer_note(summary),
    })


@app.get("/browse", response_class=HTMLResponse)
def browse_page(
    request: Request,
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
    search: str | None = Query(default=None),
    status: str | None = Query(default=None),
    org_type: str | None = Query(default=None),
    state: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
):
    limit = 25
    offset = (page - 1) * limit
    result = _queries().list_domains(
        conn, limit=limit, offset=offset,
        status=status or None, org_type=org_type or None,
        state=state or None, search=search or None,
    )
    stats = _cached("all_stats", lambda: _queries().get_dashboard_stats(conn))
    summary = _cached("summary", lambda: _queries().get_run_summary(conn))

    total = result["total"]
    range_start = offset + 1 if total else 0
    range_end = min(offset + limit, total)

    def page_url(target_page: int) -> str:
        params = {"search": search, "status": status, "org_type": org_type, "state": state, "page": target_page}
        qs = urlencode({k: v for k, v in params.items() if v})
        return f"/browse?{qs}"

    return templates.TemplateResponse(request=request, name="browse.html", context={
        "active": "browse",
        "items": result["items"],
        "total": total,
        "offset": offset,
        "range_start": range_start,
        "range_end": range_end,
        "prev_url": page_url(max(1, page - 1)),
        "next_url": page_url(page + 1),
        "filters": {"search": search, "status": status, "org_type": org_type, "state": state},
        "states": stats.get("states", []),
        "org_types": stats.get("orgTypes", []),
        "statuses": stats.get("status", []),
        "footer_note": _footer_note(summary),
    })


@app.get("/about", response_class=HTMLResponse)
def about_page(request: Request, conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    summary = _cached("summary", lambda: _queries().get_run_summary(conn))
    return templates.TemplateResponse(request=request, name="about.html", context={
        "active": "about",
        "total": summary.get("dataset_domains_total", 0),
        "footer_note": _footer_note(summary),
    })


def _reports():
    """Lazy import so the module loads cleanly before pwo/reports.py exists."""
    from . import reports  # noqa: PLC0415
    return reports


@app.get("/reports", response_class=HTMLResponse)
def reports_list_page(request: Request, conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    summary = _cached("summary", lambda: _queries().get_run_summary(conn))
    return templates.TemplateResponse(request=request, name="reports.html", context={
        "active": "reports",
        "reports": _reports().list_reports(),
        "footer_note": _footer_note(summary),
    })


@app.get("/reports/{slug}", response_class=HTMLResponse)
def report_detail_page(request: Request, slug: str, conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    report = _reports().get_report(slug)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report '{slug}' not found")
    summary = _cached("summary", lambda: _queries().get_run_summary(conn))
    return templates.TemplateResponse(request=request, name="report.html", context={
        "active": "reports",
        "report": report,
        "footer_note": _footer_note(summary),
    })


@app.get("/domains/{domain}", response_class=HTMLResponse)
def domain_page(request: Request, domain: str, conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    result = _queries().get_domain(conn, domain)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Domain '{domain}' not found")
    checked_at = _fmt_date(result.get("checked_at"))
    history = list(reversed(_queries().get_domain_history(conn, domain)))
    for h in history:
        h["checked_at_fmt"] = _fmt_date(h.get("checked_at"))
    summary = _cached("summary", lambda: _queries().get_run_summary(conn))
    return templates.TemplateResponse(request=request, name="domain.html", context={
        "d": result,
        "checked_at": checked_at,
        "history": history,
        "footer_note": _footer_note(summary),
    })


@app.get("/insights", response_class=HTMLResponse)
def insights_page(request: Request, conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    stats = _cached("all_stats", lambda: _queries().get_dashboard_stats(conn))
    summary = _cached("summary", lambda: _queries().get_run_summary(conn))
    total = stats.get("total", 0)
    status = stats.get("status", [])
    ok_count = sum(s["count"] for s in status if s["status"] == "ok")
    blocked_count = sum(s["count"] for s in status if s["status"] in _BLOCKED_STATUSES)
    return templates.TemplateResponse(request=request, name="insights.html", context={
        "active": "insights",
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
        "footer_note": _footer_note(summary),
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
        f"  <url><loc>{base}/browse</loc><changefreq>daily</changefreq></url>",
        f"  <url><loc>{base}/insights</loc><changefreq>daily</changefreq></url>",
        f"  <url><loc>{base}/reports</loc><changefreq>weekly</changefreq></url>",
        f"  <url><loc>{base}/about</loc><changefreq>monthly</changefreq></url>",
    ] + [
        f"  <url><loc>{base}/reports/{r.slug}</loc><changefreq>never</changefreq></url>"
        for r in _reports().list_reports()
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
