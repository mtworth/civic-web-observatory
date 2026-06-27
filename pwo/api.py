import os
from pathlib import Path

import duckdb
from fastapi import FastAPI, Query, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

STATIC_DIR = Path(__file__).parent / "static"


def _queries():
    """Lazy import so the module loads cleanly before pwo/queries.py exists."""
    from . import queries  # noqa: PLC0415
    return queries

DB_PATH = os.getenv("PWO_DB_PATH", "outputs/observations.duckdb")


def get_db():
    """Open a fresh read-only DuckDB connection per request.

    FastAPI runs sync handlers in a thread pool, so concurrent requests race
    on any shared connection object. DuckDB supports multiple simultaneous
    read-only openers, so per-request connections are both safe and cheap.
    """
    conn = duckdb.connect(DB_PATH, read_only=True)
    try:
        yield conn
    finally:
        conn.close()


app = FastAPI(title="Public Web Observatory", version="0.1.0")

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


@app.get("/api/summary")
def get_summary(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _queries().get_run_summary(conn)


@app.get("/api/stats/status")
def get_status_breakdown(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _queries().get_collection_status_breakdown(conn)


@app.get("/api/stats/https")
def get_https_adoption(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _queries().get_https_adoption(conn)


@app.get("/api/stats/hosting")
def get_hosting_breakdown(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _queries().get_hosting_breakdown(conn)


@app.get("/api/stats/tls")
def get_tls_stats(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _queries().get_tls_stats(conn)


@app.get("/api/stats/files")
def get_file_availability(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _queries().get_file_availability(conn)


@app.get("/api/stats/accessibility")
def get_accessibility_summary(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _queries().get_accessibility_summary(conn)


@app.get("/api/stats/blocking")
def get_blocking_breakdown(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    return _queries().get_block_type_breakdown(conn)


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
