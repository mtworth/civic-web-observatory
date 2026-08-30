"""
Exports the data this experiment needs from MotherDuck into static Parquet
files under testmigration/data/. Re-run any time you want to refresh the
snapshot the static prototype serves.

Usage:
    uv run python testmigration/export_data.py
"""

import os
from pathlib import Path

import duckdb
from dotenv import load_dotenv

load_dotenv()

OUT_DIR = Path(__file__).parent / "data"

EXPORTS = {
    "v_current": "SELECT * FROM v_current",
    "v_summary": "SELECT * FROM v_summary",
    "v_dashboard": "SELECT * FROM v_dashboard",
}


def main() -> None:
    token = os.environ.get("MOTHERDUCK_TOKEN")
    if not token:
        raise SystemExit("MOTHERDUCK_TOKEN not set (check .env)")

    con = duckdb.connect(f"md:pwo?motherduck_token={token}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for name, query in EXPORTS.items():
        out_path = OUT_DIR / f"{name}.parquet"
        con.execute(
            f"COPY ({query}) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        size_mb = out_path.stat().st_size / 1_000_000
        print(f"wrote {out_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
