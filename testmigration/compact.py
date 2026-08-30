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
    uv run python testmigration/compact.py --partitions-dir /path/to/observations --out-dir /path/to/data
"""

import argparse
import json
from pathlib import Path

import duckdb

DEFAULT_DATA_DIR = Path(__file__).parent / "data"
TECH_TOP_LIMIT = 12  # matches TECH_LIST_LIMIT in explore.html


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--partitions-dir", default=None,
        help="Directory of daily *.parquet partitions (default: testmigration/data/observations)",
    )
    parser.add_argument(
        "--out-dir", default=None,
        help="Directory to write v_current.parquet and tech_top.json into (default: testmigration/data)",
    )
    args = parser.parse_args()

    partitions_dir = Path(args.partitions_dir) if args.partitions_dir else DEFAULT_DATA_DIR / "observations"
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_DATA_DIR
    partitions_glob = str(partitions_dir / "*.parquet")
    out_path = out_dir / "v_current.parquet"
    tech_top_path = out_dir / "tech_top.json"

    partitions = sorted(partitions_dir.glob("*.parquet"))
    if not partitions:
        raise SystemExit(f"no partition files found matching {partitions_glob}")

    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(
        f"""
        COPY (
            SELECT * EXCLUDE (
                axe_ran, axe_error, axe_violations_total, axe_violations_critical,
                axe_violations_serious, axe_violations_moderate, axe_violations_minor,
                axe_rule_ids, axe_wcag_tags
            )
            FROM read_parquet('{partitions_glob}', union_by_name=true)
            QUALIFY ROW_NUMBER() OVER (PARTITION BY domain ORDER BY checked_at DESC) = 1
        ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    n_partitions = len(partitions)
    n_rows = con.execute(f"SELECT count(*) FROM read_parquet('{out_path}')").fetchone()[0]
    size_mb = out_path.stat().st_size / 1_000_000
    print(f"compacted {n_partitions} partitions -> {out_path.name}")
    print(f"{n_rows:,} domains, {size_mb:.1f} MB")

    tech_rows = con.execute(
        f"""
        SELECT t AS value, t AS label, count(*) AS count
        FROM read_parquet('{out_path}'), UNNEST(technologies) AS u(t)
        WHERE technologies IS NOT NULL AND len(technologies) > 0
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT {TECH_TOP_LIMIT}
        """
    ).fetchall()
    tech_top = [{"value": v, "label": l, "count": c} for v, l, c in tech_rows]
    tech_top_path.write_text(json.dumps(tech_top, indent=2))
    print(f"wrote {tech_top_path.name} ({len(tech_top)} technologies)")


if __name__ == "__main__":
    main()
