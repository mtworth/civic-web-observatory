import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import duckdb
from dotenv import load_dotenv

load_dotenv()
from fastapi import FastAPI, Query, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

STATIC_DIR = Path(__file__).parent / "static"

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
    internal process-level session, so subsequent opens cost ~0ms. The lifespan
    warmup below ensures that session is established before the first request.
    """
    path = _db_path()
    read_only = not path.startswith("md:")
    conn = duckdb.connect(path, read_only=read_only)
    try:
        yield conn
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the connection and ensure all views (including v_dashboard) exist.
    from .db import VIEWS  # noqa: PLC0415
    conn = duckdb.connect(_db_path())
    for view_sql in VIEWS:
        try:
            conn.execute(view_sql)
        except Exception:
            pass
    conn.close()
    yield


app = FastAPI(title="Public Web Observatory", version="0.1.0", lifespan=lifespan)

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


@app.get("/llms.txt")
def llms_txt():
    path = STATIC_DIR / "llms.txt"
    if path.exists():
        return FileResponse(path, media_type="text/plain")
    raise HTTPException(status_code=404)


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
