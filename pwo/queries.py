"""
Query layer for the Public Web Observatory DuckDB database.

All functions accept a duckdb.DuckDBPyConnection and return plain
JSON-serializable Python dicts/lists — no Pydantic, no DuckDB objects.
"""

from __future__ import annotations

import duckdb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_dict(cursor: duckdb.DuckDBPyConnection, row: tuple) -> dict:
    """Convert a single result row to a dict using cursor column names."""
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def _rows_to_dicts(cursor: duckdb.DuckDBPyConnection, rows: list[tuple]) -> list[dict]:
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in rows]


# ---------------------------------------------------------------------------
# Dashboard — all stats in one connection, one cache key
# ---------------------------------------------------------------------------

def get_dashboard_stats(conn: duckdb.DuckDBPyConnection) -> dict:
    """Return everything the dashboard needs in a single dict.

    Reads v_dashboard once (one table scan) for all scalar metrics, then
    queries the breakdown lists on the same connection. The caller gets one
    object to cache instead of eleven.
    """
    result: dict = {}

    # Scalar metrics — single scan via v_dashboard view
    try:
        cur = conn.execute("SELECT * FROM v_dashboard")
        row = cur.fetchone()
        if row:
            scalars = _row_to_dict(cur, row)
            total = scalars.get("total", 0)
            result["total"] = total
            result["https"] = {k: scalars[k] for k in
                ("https_available", "http_only", "redirects_to_https",
                 "https_pct", "redirects_pct")}
            result["tls"] = {
                "valid": scalars["tls_valid"],
                "invalid": scalars["tls_invalid"],
                "unknown": scalars["tls_unknown"],
                "expiring_30d": scalars["tls_expiring_30d"],
            }
            result["files"] = {k: scalars[k] for k in
                ("robots_txt", "sitemap_xml", "llms_txt")}
            result["files"]["total"] = total
            result["a11y"] = {
                "domains_scanned": scalars["a11y_scanned"],
                "avg_images_missing_alt": scalars["avg_images_missing_alt"],
                "pct_has_main_landmark": scalars["pct_has_main_landmark"],
                "pct_has_nav_landmark": scalars["pct_has_nav_landmark"],
                "pct_has_html_lang": scalars["pct_has_html_lang"],
                "pct_has_title": scalars["pct_has_title"],
                "avg_inputs_missing_labels": scalars["avg_inputs_missing_labels"],
            }
    except Exception:
        pass

    # List breakdowns — each is a separate GROUP BY query on v_current
    try:
        cur = conn.execute("""
            SELECT COALESCE(collection_status, 'unknown') AS status, COUNT(*) AS count
            FROM v_current GROUP BY 1 ORDER BY 2 DESC
        """)
        result["status"] = _rows_to_dicts(cur, cur.fetchall())
    except Exception:
        result["status"] = []

    try:
        cur = conn.execute("""
            SELECT COALESCE(nameserver_provider_hint, 'unknown') AS provider, COUNT(*) AS count
            FROM v_current GROUP BY 1 ORDER BY 2 DESC
        """)
        result["hosting"] = _rows_to_dicts(cur, cur.fetchall())
    except Exception:
        result["hosting"] = []

    try:
        cur = conn.execute("""
            SELECT COALESCE(homepage_block_type, 'none') AS block_type, COUNT(*) AS count
            FROM v_current GROUP BY 1 ORDER BY 2 DESC
        """)
        result["blocking"] = _rows_to_dicts(cur, cur.fetchall())
    except Exception:
        result["blocking"] = []

    try:
        cur = conn.execute("""
            SELECT t AS technology, COUNT(*) AS count
            FROM v_current, UNNEST(technologies) AS t(t)
            WHERE technologies IS NOT NULL AND len(technologies) > 0
            GROUP BY 1 ORDER BY 2 DESC LIMIT 30
        """)
        result["technologies"] = _rows_to_dicts(cur, cur.fetchall())
    except Exception:
        result["technologies"] = []

    try:
        cur = conn.execute("""
            SELECT tls_issuer AS issuer, COUNT(*) AS count
            FROM v_current WHERE tls_valid = true
            GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """)
        if "tls" in result:
            result["tls"]["top_issuers"] = _rows_to_dicts(cur, cur.fetchall())
    except Exception:
        pass

    try:
        cur = conn.execute("""
            SELECT state, COUNT(*) AS count FROM v_current
            WHERE state IS NOT NULL AND state != ''
            GROUP BY 1 ORDER BY 1
        """)
        result["states"] = _rows_to_dicts(cur, cur.fetchall())
    except Exception:
        result["states"] = []

    try:
        cur = conn.execute("""
            SELECT COALESCE(org_type, 'unknown') AS org_type, COUNT(*) AS count
            FROM v_current GROUP BY 1 ORDER BY 2 DESC
        """)
        result["orgTypes"] = _rows_to_dicts(cur, cur.fetchall())
    except Exception:
        result["orgTypes"] = []

    return result


# ---------------------------------------------------------------------------
# Run summary
# ---------------------------------------------------------------------------

def get_run_summary(conn: duckdb.DuckDBPyConnection, run_id: str | None = None) -> dict:
    """Return the most recent run_summary row, or a specific run by run_id.

    Also attaches dataset_domains_total / dataset_last_checked_at, computed
    from v_current rather than the run row itself — a single run (e.g. a
    small incremental re-crawl) covers only a subset of domains, so its own
    domains_total must not be mistaken for the size of the whole tracked
    dataset.

    Returns an empty dict if no runs exist yet.
    """
    try:
        if run_id is not None:
            cur = conn.execute(
                "SELECT * FROM run_summary WHERE run_id = ? LIMIT 1",
                [run_id],
            )
        else:
            cur = conn.execute(
                "SELECT * FROM run_summary ORDER BY finished_at DESC LIMIT 1"
            )
        row = cur.fetchone()
    except Exception:
        return {}

    if row is None:
        return {}

    result = _row_to_dict(cur, row)
    # Convert datetime objects to ISO strings for JSON safety
    for key, val in result.items():
        if hasattr(val, "isoformat"):
            result[key] = val.isoformat()

    try:
        dataset_total, dataset_last_checked = conn.execute(
            "SELECT COUNT(*), MAX(checked_at) FROM v_current"
        ).fetchone()
        result["dataset_domains_total"] = dataset_total
        result["dataset_last_checked_at"] = (
            dataset_last_checked.isoformat() if dataset_last_checked else None
        )
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# Collection status breakdown
# ---------------------------------------------------------------------------

def get_collection_status_breakdown(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Return [{status, count}, ...] sorted by count descending."""
    try:
        cur = conn.execute("""
            SELECT
                COALESCE(collection_status, 'unknown') AS status,
                COUNT(*) AS count
            FROM v_current
            GROUP BY 1
            ORDER BY 2 DESC
        """)
        return _rows_to_dicts(cur, cur.fetchall())
    except Exception:
        return []


# ---------------------------------------------------------------------------
# HTTPS adoption
# ---------------------------------------------------------------------------

def get_https_adoption(conn: duckdb.DuckDBPyConnection) -> dict:
    """Return aggregate HTTPS / redirect counts and percentages."""
    try:
        cur = conn.execute("""
            SELECT
                COUNT(*) FILTER (WHERE https_available = true)  AS https_available,
                COUNT(*) FILTER (WHERE https_available = false
                                    OR https_available IS NULL) AS http_only,
                COUNT(*) FILTER (WHERE http_redirects_to_https = true) AS redirects_to_https,
                COUNT(*) AS total,
                ROUND(
                    100.0
                    * COUNT(*) FILTER (WHERE https_available = true)
                    / NULLIF(COUNT(*), 0),
                    1
                ) AS https_pct,
                ROUND(
                    100.0
                    * COUNT(*) FILTER (WHERE http_redirects_to_https = true)
                    / NULLIF(COUNT(*), 0),
                    1
                ) AS redirects_pct
            FROM v_current
        """)
        row = cur.fetchone()
    except Exception:
        return {}

    if row is None:
        return {}
    return _row_to_dict(cur, row)


# ---------------------------------------------------------------------------
# Hosting breakdown
# ---------------------------------------------------------------------------

def get_hosting_breakdown(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Return [{provider, count}, ...] sorted by count descending."""
    try:
        cur = conn.execute("""
            SELECT
                COALESCE(nameserver_provider_hint, 'unknown') AS provider,
                COUNT(*) AS count
            FROM v_current
            GROUP BY 1
            ORDER BY 2 DESC
        """)
        return _rows_to_dicts(cur, cur.fetchall())
    except Exception:
        return []


# ---------------------------------------------------------------------------
# TLS stats
# ---------------------------------------------------------------------------

def get_tls_stats(conn: duckdb.DuckDBPyConnection) -> dict:
    """Return TLS validity counts, expiry warning count, and top issuers."""
    try:
        cur = conn.execute("""
            SELECT
                COUNT(*) FILTER (WHERE tls_valid = true)  AS valid,
                COUNT(*) FILTER (WHERE tls_valid = false) AS invalid,
                COUNT(*) FILTER (WHERE tls_valid IS NULL) AS unknown,
                COUNT(*) FILTER (
                    WHERE tls_valid = true
                      AND tls_days_until_expiry IS NOT NULL
                      AND tls_days_until_expiry < 30
                ) AS expiring_30d
            FROM v_current
        """)
        agg = _row_to_dict(cur, cur.fetchone())

        cur2 = conn.execute("""
            SELECT
                COALESCE(tls_issuer, 'unknown') AS issuer,
                COUNT(*) AS count
            FROM v_current
            WHERE tls_valid = true
            GROUP BY 1
            ORDER BY 2 DESC
            LIMIT 10
        """)
        agg["top_issuers"] = _rows_to_dicts(cur2, cur2.fetchall())
    except Exception:
        return {}

    return agg


# ---------------------------------------------------------------------------
# File availability
# ---------------------------------------------------------------------------

def get_file_availability(conn: duckdb.DuckDBPyConnection) -> dict:
    """Return counts for robots.txt, sitemap.xml, and llms.txt availability."""
    try:
        cur = conn.execute("""
            SELECT
                COUNT(*) FILTER (WHERE robots_txt_available = true)  AS robots_txt,
                COUNT(*) FILTER (WHERE sitemap_xml_available = true) AS sitemap_xml,
                COUNT(*) FILTER (WHERE llms_txt_available = true)    AS llms_txt,
                COUNT(*) AS total
            FROM v_current
        """)
        row = cur.fetchone()
    except Exception:
        return {}

    if row is None:
        return {}
    return _row_to_dict(cur, row)


# ---------------------------------------------------------------------------
# Block type breakdown
# ---------------------------------------------------------------------------

def get_block_type_breakdown(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Return [{block_type, count}, ...] for all block types (including 'none'), sorted by count desc."""
    try:
        cur = conn.execute("""
            SELECT
                COALESCE(homepage_block_type, 'none') AS block_type,
                COUNT(*) AS count
            FROM v_current
            GROUP BY 1
            ORDER BY 2 DESC
        """)
        return _rows_to_dicts(cur, cur.fetchall())
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Blocked domains
# ---------------------------------------------------------------------------

def get_blocked_domains(
    conn: duckdb.DuckDBPyConnection,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Return a paginated list of blocked domains.

    Each item includes: domain, homepage_block_type, collection_status, org_name.
    """
    try:
        cur = conn.execute(
            """
            SELECT
                domain,
                homepage_block_type,
                collection_status,
                org_name
            FROM v_current
            WHERE homepage_block_type IS NOT NULL
              AND homepage_block_type != 'none'
            ORDER BY domain
            LIMIT ? OFFSET ?
            """,
            [limit, offset],
        )
        return _rows_to_dicts(cur, cur.fetchall())
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Domain listing
# ---------------------------------------------------------------------------

def list_domains(
    conn: duckdb.DuckDBPyConnection,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    sector: str | None = None,
    org_type: str | None = None,
    state: str | None = None,
    search: str | None = None,
) -> dict:
    """Return a paginated, filterable list of domain observations.

    Supports filtering by collection_status, sector, org_type, state, and an
    ILIKE search over domain and org_name. Returns {"total": N, "items": [...]}.
    """
    where_clauses = []
    params: list = []

    if status is not None:
        where_clauses.append("collection_status = ?")
        params.append(status)

    if sector is not None:
        where_clauses.append("sector = ?")
        params.append(sector)

    if org_type is not None:
        where_clauses.append("org_type = ?")
        params.append(org_type)

    if state is not None:
        where_clauses.append("state = ?")
        params.append(state)

    if search is not None:
        where_clauses.append("(domain ILIKE ? OR org_name ILIKE ?)")
        pattern = f"%{search}%"
        params.extend([pattern, pattern])

    # Check whether the sector column exists (it may be absent on DBs that
    # were not opened via open_db, which runs the migration).
    try:
        col_names = {c[0] for c in conn.execute("DESCRIBE observations").fetchall()}
        has_sector = "sector" in col_names
    except Exception:
        has_sector = False

    # If sector column is absent, drop any sector filter to avoid SQL errors.
    if not has_sector and sector is not None:
        # Remove the sector clause we may have added above
        where_clauses = [c for c in where_clauses if "sector" not in c]
        params_without_sector: list = []
        # Rebuild params excluding the sector value
        for clause in where_clauses:
            if clause == "collection_status = ?":
                params_without_sector.append(status)
            elif "(domain ILIKE ? OR org_name ILIKE ?)" in clause:
                pattern = f"%{search}%"
                params_without_sector.extend([pattern, pattern])
        params = params_without_sector

    sector_col = "sector," if has_sector else "NULL AS sector,"
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    try:
        count_cur = conn.execute(
            f"SELECT COUNT(*) FROM v_current {where_sql}",
            params,
        )
        total = count_cur.fetchone()[0]

        items_cur = conn.execute(
            f"""
            SELECT
                domain,
                org_name,
                org_type,
                state,
                {sector_col}
                collection_status,
                homepage_block_type,
                https_available,
                tls_valid,
                tls_days_until_expiry,
                robots_txt_available,
                sitemap_xml_available,
                llms_txt_available,
                nameserver_provider_hint,
                checked_at
            FROM v_current
            {where_sql}
            ORDER BY domain
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        )
        rows = items_cur.fetchall()
        items = []
        cols = [d[0] for d in items_cur.description]
        for row in rows:
            rec = dict(zip(cols, row))
            if rec.get("checked_at") and hasattr(rec["checked_at"], "isoformat"):
                rec["checked_at"] = rec["checked_at"].isoformat()
            items.append(rec)
    except Exception:
        return {"total": 0, "items": []}

    return {"total": total, "items": items}


# ---------------------------------------------------------------------------
# Single domain lookup
# ---------------------------------------------------------------------------

def get_domain(conn: duckdb.DuckDBPyConnection, domain: str) -> dict | None:
    """Return the latest observation row for a single domain, or None."""
    try:
        cur = conn.execute(
            "SELECT * FROM v_current WHERE domain = ? LIMIT 1",
            [domain],
        )
        row = cur.fetchone()
    except Exception:
        return None

    if row is None:
        return None

    result = _row_to_dict(cur, row)
    # Convert datetime and list-like objects for JSON safety
    for key, val in result.items():
        if hasattr(val, "isoformat"):
            result[key] = val.isoformat()
    return result


# ---------------------------------------------------------------------------
# Domain history (time series)
# ---------------------------------------------------------------------------

def get_domain_history(conn: duckdb.DuckDBPyConnection, domain: str) -> list[dict]:
    """Return every historical observation for a domain, oldest first.

    Reads from the raw append-only observations table (not v_current) so
    callers can see how a domain's signals changed across crawl runs.
    """
    try:
        cur = conn.execute(
            """
            SELECT
                run_id,
                checked_at,
                collection_status,
                homepage_status_code,
                homepage_block_type,
                https_available,
                tls_valid,
                tls_days_until_expiry,
                robots_txt_available,
                sitemap_xml_available,
                llms_txt_available,
                nameserver_provider_hint,
                homepage_response_time_ms
            FROM observations
            WHERE domain = ?
            ORDER BY checked_at ASC
            """,
            [domain],
        )
        rows = cur.fetchall()
    except Exception:
        return []

    results = _rows_to_dicts(cur, rows)
    for rec in results:
        if rec.get("checked_at") and hasattr(rec["checked_at"], "isoformat"):
            rec["checked_at"] = rec["checked_at"].isoformat()
    return results


# ---------------------------------------------------------------------------
# State / org_type breakdowns (for browse navigation)
# ---------------------------------------------------------------------------

def get_state_breakdown(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Return [{state, count}, ...] for domains with a known state, sorted by state."""
    try:
        cur = conn.execute("""
            SELECT state, COUNT(*) AS count
            FROM v_current
            WHERE state IS NOT NULL AND state != ''
            GROUP BY 1
            ORDER BY 1
        """)
        return _rows_to_dicts(cur, cur.fetchall())
    except Exception:
        return []


def get_org_type_breakdown(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Return [{org_type, count}, ...] sorted by count descending."""
    try:
        cur = conn.execute("""
            SELECT
                COALESCE(org_type, 'unknown') AS org_type,
                COUNT(*) AS count
            FROM v_current
            GROUP BY 1
            ORDER BY 2 DESC
        """)
        return _rows_to_dicts(cur, cur.fetchall())
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Accessibility summary
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Technology breakdown
# ---------------------------------------------------------------------------

def get_tech_breakdown(conn: duckdb.DuckDBPyConnection, limit: int = 30) -> list[dict]:
    """Return [{technology, count}, ...] sorted by count descending."""
    try:
        cur = conn.execute("""
            SELECT t AS technology, COUNT(*) AS count
            FROM v_current, UNNEST(technologies) AS t(t)
            WHERE technologies IS NOT NULL AND len(technologies) > 0
            GROUP BY 1
            ORDER BY 2 DESC
            LIMIT ?
        """, [limit])
        return _rows_to_dicts(cur, cur.fetchall())
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Accessibility summary
# ---------------------------------------------------------------------------

def get_accessibility_summary(conn: duckdb.DuckDBPyConnection) -> dict:
    """Return aggregate static a11y metrics across all scanned domains."""
    try:
        cur = conn.execute("""
            SELECT
                COUNT(*) FILTER (WHERE static_a11y_ran = true) AS domains_scanned,
                ROUND(AVG(a11y_images_missing_alt), 2)          AS avg_images_missing_alt,
                ROUND(
                    100.0
                    * COUNT(*) FILTER (WHERE a11y_has_main_landmark = true)
                    / NULLIF(COUNT(*) FILTER (WHERE static_a11y_ran = true), 0),
                    1
                ) AS pct_has_main_landmark,
                ROUND(
                    100.0
                    * COUNT(*) FILTER (WHERE a11y_has_nav_landmark = true)
                    / NULLIF(COUNT(*) FILTER (WHERE static_a11y_ran = true), 0),
                    1
                ) AS pct_has_nav_landmark,
                ROUND(
                    100.0
                    * COUNT(*) FILTER (WHERE a11y_has_html_lang = true)
                    / NULLIF(COUNT(*) FILTER (WHERE static_a11y_ran = true), 0),
                    1
                ) AS pct_has_html_lang,
                ROUND(
                    100.0
                    * COUNT(*) FILTER (WHERE a11y_has_title = true)
                    / NULLIF(COUNT(*) FILTER (WHERE static_a11y_ran = true), 0),
                    1
                ) AS pct_has_title,
                ROUND(AVG(a11y_inputs_missing_labels), 2) AS avg_inputs_missing_labels,
                SUM(axe_violations_critical)  AS axe_violations_critical_total,
                SUM(axe_violations_serious)   AS axe_violations_serious_total,
                SUM(axe_violations_moderate)  AS axe_violations_moderate_total,
                SUM(axe_violations_minor)     AS axe_violations_minor_total,
                COUNT(*) FILTER (WHERE axe_ran = true) AS axe_scanned
            FROM v_current
        """)
        row = cur.fetchone()
    except Exception:
        return {}

    if row is None:
        return {}
    return _row_to_dict(cur, row)
