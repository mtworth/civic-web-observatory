"""
Simulates what the continuous daily crawl would produce as raw, immutable,
append-only partitions: one small Parquet file per crawl day, under
data/observations/<date>.parquet.

This is a stand-in for what a rewritten crawler.py would write directly
(one partition per run) instead of inserting into MotherDuck. For this
experiment we still source the historical rows from MotherDuck (that's
where they currently live) and split them out by day to prove the
partition + compaction design works end-to-end.

Usage:
    uv run python site/partition_export.py
"""

import os
from pathlib import Path

import duckdb
from dotenv import load_dotenv

load_dotenv()

OUT_DIR = Path(__file__).parent / "data" / "observations"


def main() -> None:
    token = os.environ.get("MOTHERDUCK_TOKEN")
    if not token:
        raise SystemExit("MOTHERDUCK_TOKEN not set (check .env)")

    con = duckdb.connect(f"md:pwo?motherduck_token={token}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    days = con.execute(
        "SELECT DISTINCT date_trunc('day', checked_at) AS day FROM observations ORDER BY 1"
    ).fetchall()

    for (day,) in days:
        date_str = day.strftime("%Y-%m-%d")
        out_path = OUT_DIR / f"{date_str}.parquet"
        con.execute(
            f"""
            COPY (
                SELECT * FROM observations
                WHERE date_trunc('day', checked_at) = TIMESTAMP '{date_str}'
            ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        size_kb = out_path.stat().st_size / 1_000
        n = con.execute(f"SELECT count(*) FROM read_parquet('{out_path}')").fetchone()[0]
        print(f"wrote {out_path.name}: {n} rows, {size_kb:.0f} KB")

    print(f"\n{len(days)} daily partitions written to {OUT_DIR}")


if __name__ == "__main__":
    main()
