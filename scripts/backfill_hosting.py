"""
Backfill nameserver_provider_hint with raw GeoLite2-ASN org names.

Uses a JOIN-based UPDATE: resolves all IPs locally, uploads a lookup
table to the target DB in one batch, then runs a single SQL UPDATE.
Works efficiently against MotherDuck.

Usage:
    uv run python scripts/backfill_hosting.py [--db PATH] [--dry-run]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb
import geoip2.database
import geoip2.errors

GEOIP_PATH = Path("data/GeoLite2-ASN.mmdb")


def backfill(db_path: str, dry_run: bool) -> None:
    if not GEOIP_PATH.exists():
        print(f"Error: {GEOIP_PATH} not found. Run `uv run pwo download-geoip` first.")
        sys.exit(1)

    conn = duckdb.connect(db_path)

    print("Fetching distinct IPs from observations...")
    rows = conn.execute("""
        SELECT DISTINCT primary_ip
        FROM observations
        WHERE primary_ip IS NOT NULL
    """).fetchall()
    ips = [r[0] for r in rows]
    print(f"  {len(ips):,} distinct IPs to resolve")

    print("Resolving via GeoLite2...")
    lookup: list[tuple[str, str | None]] = []
    with geoip2.database.Reader(str(GEOIP_PATH)) as reader:
        for ip in ips:
            try:
                record = reader.asn(ip)
                org = record.autonomous_system_organization
            except (geoip2.errors.AddressNotFoundError, Exception):
                org = None
            lookup.append((ip, org))

    resolved = sum(1 for _, org in lookup if org)
    print(f"  {resolved:,} IPs resolved, {len(lookup) - resolved:,} unresolved")

    if dry_run:
        print("\nDry run — no changes written.")
        print("\nSample mappings (first 20):")
        for ip, org in lookup[:20]:
            print(f"  {ip} -> {org!r}")
        return

    print("Uploading lookup table...")
    conn.execute("CREATE OR REPLACE TEMP TABLE _ip_org (ip VARCHAR, org VARCHAR)")
    conn.executemany("INSERT INTO _ip_org VALUES (?, ?)", lookup)

    print("Running JOIN-based UPDATE...")
    result = conn.execute("""
        UPDATE observations
        SET nameserver_provider_hint = _ip_org.org
        FROM _ip_org
        WHERE observations.primary_ip = _ip_org.ip
          AND (observations.nameserver_provider_hint IS DISTINCT FROM _ip_org.org)
    """)

    conn.execute("DROP TABLE IF EXISTS _ip_org")
    print(f"Done. {result.rowcount if hasattr(result, 'rowcount') else '?'} rows updated.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="outputs/observations.duckdb")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    backfill(args.db, args.dry_run)


if __name__ == "__main__":
    main()
