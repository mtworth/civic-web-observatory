import json
from pathlib import Path
from datetime import datetime

import duckdb

from .models import DomainObservation

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    domain VARCHAR,
    source VARCHAR,
    org_name VARCHAR,
    org_type VARCHAR,
    state VARCHAR,
    ein VARCHAR,
    checked_at TIMESTAMP WITH TIME ZONE,
    crawler_version VARCHAR,

    dns_resolves BOOLEAN,
    dns_error VARCHAR,
    a_records VARCHAR[],
    aaaa_records VARCHAR[],
    cname_records VARCHAR[],
    ns_records VARCHAR[],

    homepage_attempted BOOLEAN,
    homepage_url VARCHAR,
    homepage_final_url VARCHAR,
    homepage_status_code INTEGER,
    homepage_content_type VARCHAR,
    homepage_response_time_ms INTEGER,
    homepage_size_bytes INTEGER,
    homepage_redirect_count INTEGER,
    homepage_error VARCHAR,
    homepage_block_type VARCHAR,
    homepage_ua_retry_succeeded BOOLEAN,

    https_available BOOLEAN,
    http_available BOOLEAN,
    http_redirects_to_https BOOLEAN,
    tls_valid BOOLEAN,
    tls_error VARCHAR,
    tls_issuer VARCHAR,
    tls_not_before VARCHAR,
    tls_not_after VARCHAR,
    tls_days_until_expiry INTEGER,

    robots_txt_status_code INTEGER,
    robots_txt_available BOOLEAN,
    robots_txt_size_bytes INTEGER,
    robots_txt_error VARCHAR,
    robots_allows_homepage BOOLEAN,
    robots_sitemaps_listed INTEGER,

    sitemap_xml_status_code INTEGER,
    sitemap_xml_available BOOLEAN,
    sitemap_xml_size_bytes INTEGER,
    sitemap_xml_url_count INTEGER,
    sitemap_xml_error VARCHAR,

    llms_txt_status_code INTEGER,
    llms_txt_available BOOLEAN,
    llms_txt_size_bytes INTEGER,
    llms_txt_has_markdown_headings BOOLEAN,
    llms_txt_link_count INTEGER,
    llms_txt_error VARCHAR,

    page_title VARCHAR,
    page_title_present BOOLEAN,
    meta_description_present BOOLEAN,
    html_lang_present BOOLEAN,
    canonical_url VARCHAR,
    mobile_viewport_present BOOLEAN,
    h1_count INTEGER,

    primary_ip VARCHAR,
    host_asn VARCHAR,
    host_asn_org VARCHAR,
    host_network_name VARCHAR,
    nameserver_provider_hint VARCHAR,
    cname_provider_hint VARCHAR,

    static_a11y_ran BOOLEAN,
    static_a11y_error VARCHAR,
    a11y_has_title BOOLEAN,
    a11y_has_html_lang BOOLEAN,
    a11y_h1_count INTEGER,
    a11y_images_total INTEGER,
    a11y_images_missing_alt INTEGER,
    a11y_inputs_total INTEGER,
    a11y_inputs_missing_labels INTEGER,
    a11y_has_main_landmark BOOLEAN,
    a11y_has_nav_landmark BOOLEAN,

    is_probably_parked BOOLEAN,
    parked_reason VARCHAR,
    social_only_redirect BOOLEAN,
    social_only_platform VARCHAR,

    error_type VARCHAR,
    error_message VARCHAR,
    stage VARCHAR,

    collection_status VARCHAR,
    sector VARCHAR,
    run_id VARCHAR,
    technologies VARCHAR[]
)
"""

SECTOR_MIGRATION = """
ALTER TABLE observations ADD COLUMN IF NOT EXISTS sector VARCHAR
"""

RUN_ID_MIGRATION = """
ALTER TABLE observations ADD COLUMN IF NOT EXISTS run_id VARCHAR
"""

UA_RETRY_MIGRATION = """
ALTER TABLE observations ADD COLUMN IF NOT EXISTS homepage_ua_retry_succeeded BOOLEAN
"""

TECHNOLOGIES_MIGRATION = "ALTER TABLE observations ADD COLUMN IF NOT EXISTS technologies VARCHAR[]"

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_domain ON observations (domain)",
    "CREATE INDEX IF NOT EXISTS idx_status ON observations (collection_status)",
    "CREATE INDEX IF NOT EXISTS idx_checked_at ON observations (checked_at)",
    "CREATE INDEX IF NOT EXISTS idx_sector ON observations (sector)",
    "CREATE INDEX IF NOT EXISTS idx_run_id ON observations (run_id)",
]

VIEWS = [
    # v_current: latest observation per domain. Observations is an
    # append-only history table (one row per domain per crawl run); every
    # "snapshot" query (dashboards, listings, single-domain lookups) should
    # read from this view, not the raw table, or repeated runs will
    # double-count domains and single-domain lookups become ambiguous.
    """
CREATE OR REPLACE VIEW v_current AS
SELECT *
FROM observations
QUALIFY ROW_NUMBER() OVER (PARTITION BY domain ORDER BY checked_at DESC) = 1
""",
    # v_dashboard: all scalar dashboard metrics in one table scan of v_current.
    # The /api/stats endpoint reads this view once, then queries the breakdown
    # lists (status, hosting, blocking, technologies) on the same connection.
    """
CREATE OR REPLACE VIEW v_dashboard AS
SELECT
    COUNT(*)                                                              AS total,
    COUNT(*) FILTER (WHERE https_available = true)                        AS https_available,
    COUNT(*) FILTER (WHERE https_available = false
                        OR https_available IS NULL)                       AS http_only,
    COUNT(*) FILTER (WHERE http_redirects_to_https = true)               AS redirects_to_https,
    ROUND(100.0 * COUNT(*) FILTER (WHERE https_available = true)
          / NULLIF(COUNT(*), 0), 1)                                      AS https_pct,
    ROUND(100.0 * COUNT(*) FILTER (WHERE http_redirects_to_https = true)
          / NULLIF(COUNT(*), 0), 1)                                      AS redirects_pct,
    COUNT(*) FILTER (WHERE tls_valid = true)                             AS tls_valid,
    COUNT(*) FILTER (WHERE tls_valid = false)                            AS tls_invalid,
    COUNT(*) FILTER (WHERE tls_valid IS NULL)                            AS tls_unknown,
    COUNT(*) FILTER (WHERE tls_valid = true
                      AND tls_days_until_expiry IS NOT NULL
                      AND tls_days_until_expiry < 30)                    AS tls_expiring_30d,
    COUNT(*) FILTER (WHERE robots_txt_available = true)                  AS robots_txt,
    COUNT(*) FILTER (WHERE sitemap_xml_available = true)                 AS sitemap_xml,
    COUNT(*) FILTER (WHERE llms_txt_available = true)                    AS llms_txt,
    COUNT(*) FILTER (WHERE static_a11y_ran = true)                       AS a11y_scanned,
    ROUND(AVG(a11y_images_missing_alt), 2)                               AS avg_images_missing_alt,
    ROUND(100.0 * COUNT(*) FILTER (WHERE a11y_has_main_landmark = true)
          / NULLIF(COUNT(*) FILTER (WHERE static_a11y_ran = true), 0), 1) AS pct_has_main_landmark,
    ROUND(100.0 * COUNT(*) FILTER (WHERE a11y_has_nav_landmark = true)
          / NULLIF(COUNT(*) FILTER (WHERE static_a11y_ran = true), 0), 1) AS pct_has_nav_landmark,
    ROUND(100.0 * COUNT(*) FILTER (WHERE a11y_has_html_lang = true)
          / NULLIF(COUNT(*) FILTER (WHERE static_a11y_ran = true), 0), 1) AS pct_has_html_lang,
    ROUND(100.0 * COUNT(*) FILTER (WHERE a11y_has_title = true)
          / NULLIF(COUNT(*) FILTER (WHERE static_a11y_ran = true), 0), 1) AS pct_has_title,
    ROUND(AVG(a11y_inputs_missing_labels), 2)                            AS avg_inputs_missing_labels
FROM v_current
""",
    """
CREATE OR REPLACE VIEW v_summary AS
SELECT
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE collection_status = 'ok') AS ok,
    COUNT(*) FILTER (WHERE collection_status = 'blocked') AS blocked,
    COUNT(*) FILTER (WHERE collection_status = 'dns_failed') AS dns_failed,
    COUNT(*) FILTER (WHERE collection_status = 'tls_failed') AS tls_failed,
    COUNT(*) FILTER (WHERE collection_status = 'timeout') AS timeout,
    COUNT(*) FILTER (WHERE collection_status = 'connection_refused') AS connection_refused,
    COUNT(*) FILTER (WHERE collection_status = 'unknown_error') AS unknown_error,
    COUNT(*) FILTER (WHERE https_available = true) AS https_available,
    COUNT(*) FILTER (WHERE tls_valid = true) AS tls_valid,
    COUNT(*) FILTER (WHERE robots_txt_available = true) AS robots_txt_available,
    COUNT(*) FILTER (WHERE sitemap_xml_available = true) AS sitemap_xml_available,
    COUNT(*) FILTER (WHERE llms_txt_available = true) AS llms_txt_available
FROM v_current
""",
    """
CREATE OR REPLACE VIEW v_https_adoption AS
SELECT
    COUNT(*) FILTER (WHERE https_available = true) AS https_available,
    COUNT(*) FILTER (WHERE https_available = false OR https_available IS NULL) AS http_only,
    COUNT(*) FILTER (WHERE http_redirects_to_https = true) AS redirects_to_https,
    COUNT(*) AS total,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE https_available = true) / NULLIF(COUNT(*), 0),
        1
    ) AS https_pct,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE http_redirects_to_https = true) / NULLIF(COUNT(*), 0),
        1
    ) AS redirects_pct
FROM v_current
""",
    """
CREATE OR REPLACE VIEW v_hosting_breakdown AS
SELECT
    COALESCE(nameserver_provider_hint, 'unknown') AS provider,
    COUNT(*) AS count
FROM v_current
GROUP BY provider
ORDER BY count DESC
""",
    """
CREATE OR REPLACE VIEW v_tls_expiring AS
SELECT *
FROM v_current
WHERE tls_valid = true
  AND tls_days_until_expiry IS NOT NULL
  AND tls_days_until_expiry < 30
""",
    """
CREATE OR REPLACE VIEW v_blocked AS
SELECT *
FROM v_current
WHERE homepage_block_type IS NOT NULL
  AND homepage_block_type != 'none'
""",
    """
CREATE OR REPLACE VIEW v_file_availability AS
SELECT
    COUNT(*) FILTER (WHERE robots_txt_available = true) AS robots_txt_available,
    COUNT(*) FILTER (WHERE sitemap_xml_available = true) AS sitemap_xml_available,
    COUNT(*) FILTER (WHERE llms_txt_available = true) AS llms_txt_available,
    COUNT(*) AS total
FROM v_current
""",
]

RUN_SUMMARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_summary (
    run_id VARCHAR,
    started_at TIMESTAMP WITH TIME ZONE,
    finished_at TIMESTAMP WITH TIME ZONE,
    duration_seconds DOUBLE,
    domains_total INTEGER,
    domains_ok INTEGER,
    domains_failed INTEGER,
    domains_blocked INTEGER,
    domains_with_robots_txt INTEGER,
    domains_with_sitemap_xml INTEGER,
    domains_with_llms_txt INTEGER,
    domains_with_valid_tls INTEGER,
    average_response_time_ms DOUBLE,
    crawler_version VARCHAR
)
"""


def _is_motherduck(db_path: str) -> bool:
    return db_path.startswith("md:")


def open_db(db_path: str) -> duckdb.DuckDBPyConnection:
    if _is_motherduck(db_path):
        conn = duckdb.connect(db_path)
    else:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = duckdb.connect(db_path)
        conn.execute(SCHEMA)
        conn.execute(RUN_SUMMARY_SCHEMA)
        # Migrate new columns onto existing tables (all idempotent via ADD COLUMN IF NOT EXISTS)
        conn.execute(SECTOR_MIGRATION)
        conn.execute(RUN_ID_MIGRATION)
        conn.execute(UA_RETRY_MIGRATION)
        conn.execute(TECHNOLOGIES_MIGRATION)
        # Indexes (idempotent)
        for idx_sql in INDEXES:
            conn.execute(idx_sql)
    # Views (idempotent via CREATE OR REPLACE) — runs for both local and MotherDuck
    for view_sql in VIEWS:
        conn.execute(view_sql)
    return conn


def insert_observation(conn: duckdb.DuckDBPyConnection, obs: DomainObservation) -> None:
    data = obs.model_dump()
    columns = list(data.keys())
    placeholders = ", ".join(["?" for _ in columns])
    col_names = ", ".join(columns)
    values = [data[c] for c in columns]
    conn.execute(f"INSERT INTO observations ({col_names}) VALUES ({placeholders})", values)


def write_run_summary(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    crawler_version: str,
) -> dict:
    duration = (finished_at - started_at).total_seconds()

    summary = conn.execute("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE collection_status = 'ok') AS ok,
            COUNT(*) FILTER (WHERE collection_status IN (
                'dns_failed','tls_failed','timeout','connection_refused','unknown_error'
            )) AS failed,
            COUNT(*) FILTER (WHERE homepage_block_type != 'none') AS blocked,
            COUNT(*) FILTER (WHERE robots_txt_available = true) AS with_robots,
            COUNT(*) FILTER (WHERE sitemap_xml_available = true) AS with_sitemap,
            COUNT(*) FILTER (WHERE llms_txt_available = true) AS with_llms,
            COUNT(*) FILTER (WHERE tls_valid = true) AS with_tls,
            AVG(homepage_response_time_ms) AS avg_response_ms
        FROM observations
        WHERE run_id = ?
    """, [run_id]).fetchone()

    (total, ok, failed, blocked, with_robots, with_sitemap,
     with_llms, with_tls, avg_ms) = summary

    row = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": duration,
        "domains_total": total,
        "domains_ok": ok,
        "domains_failed": failed,
        "domains_blocked": blocked,
        "domains_with_robots_txt": with_robots,
        "domains_with_sitemap_xml": with_sitemap,
        "domains_with_llms_txt": with_llms,
        "domains_with_valid_tls": with_tls,
        "average_response_time_ms": round(avg_ms, 1) if avg_ms else None,
        "crawler_version": crawler_version,
    }

    cols = ", ".join(row.keys())
    placeholders = ", ".join(["?" for _ in row])
    conn.execute(f"INSERT INTO run_summary ({cols}) VALUES ({placeholders})", list(row.values()))

    return row
