"""
Compacts the partitioned raw observations (data/observations/*.parquet)
into a single flat "latest observation per domain" snapshot
(data/v_current.parquet) — the file the static site actually reads.

This is the MotherDuck-free replacement for the `v_current` view in
pwo/db.py (same QUALIFY ROW_NUMBER() dedup logic, same semantics). It only
touches local files — no DB connection, no token, nothing but DuckDB
reading Parquet from disk. This is the step a CI job would run after
appending each day's partition, before publishing the snapshot as a
GitHub Release asset.

Also writes data/tech_top.json — the default (unfiltered) technology
breakdown Explore's sidebar shows on load, precomputed here instead of
running a live UNNEST scan in the browser on every page load. That scan
measured ~1.2s in DuckDB-WASM (single-threaded, no COOP/COEP headers to
enable the multi-threaded build) against 227k rows — by far the slowest
single query on the page, more than every other sidebar/table query
combined. A free-text search still needs the corpus live in-browser (it
has to reach technologies outside this precomputed top set), so that path
is untouched — this only short-circuits the common case: the default view
before anyone has typed anything.

Usage:
    uv run python testmigration/compact.py
"""

import json
from pathlib import Path

import duckdb

DATA_DIR = Path(__file__).parent / "data"
PARTITIONS_GLOB = str(DATA_DIR / "observations" / "*.parquet")
OUT_PATH = DATA_DIR / "v_current.parquet"
TECH_TOP_PATH = DATA_DIR / "tech_top.json"
TECH_TOP_LIMIT = 12  # matches TECH_LIST_LIMIT in explore.html


def main() -> None:
    partitions = sorted(Path(DATA_DIR / "observations").glob("*.parquet"))
    if not partitions:
        raise SystemExit(f"no partition files found matching {PARTITIONS_GLOB}")

    con = duckdb.connect()
    con.execute(
        f"""
        COPY (
            SELECT *
            FROM read_parquet('{PARTITIONS_GLOB}')
            QUALIFY ROW_NUMBER() OVER (PARTITION BY domain ORDER BY checked_at DESC) = 1
        ) TO '{OUT_PATH}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    n_partitions = len(partitions)
    n_rows = con.execute(f"SELECT count(*) FROM read_parquet('{OUT_PATH}')").fetchone()[0]
    size_mb = OUT_PATH.stat().st_size / 1_000_000
    print(f"compacted {n_partitions} partitions -> {OUT_PATH.name}")
    print(f"{n_rows:,} domains, {size_mb:.1f} MB")

    tech_rows = con.execute(
        f"""
        SELECT t AS value, t AS label, count(*) AS count
        FROM read_parquet('{OUT_PATH}'), UNNEST(technologies) AS u(t)
        WHERE technologies IS NOT NULL AND len(technologies) > 0
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT {TECH_TOP_LIMIT}
        """
    ).fetchall()
    tech_top = [{"value": v, "label": l, "count": c} for v, l, c in tech_rows]
    TECH_TOP_PATH.write_text(json.dumps(tech_top, indent=2))
    print(f"wrote {TECH_TOP_PATH.name} ({len(tech_top)} technologies)")


if __name__ == "__main__":
    main()
