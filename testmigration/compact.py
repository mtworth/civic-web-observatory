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

Usage:
    uv run python testmigration/compact.py
"""

from pathlib import Path

import duckdb

DATA_DIR = Path(__file__).parent / "data"
PARTITIONS_GLOB = str(DATA_DIR / "observations" / "*.parquet")
OUT_PATH = DATA_DIR / "v_current.parquet"


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


if __name__ == "__main__":
    main()
