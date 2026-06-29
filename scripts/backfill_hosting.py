"""
Backfill nameserver_provider_hint with raw GeoLite2-ASN org names.

Replaces all existing hints with the authoritative ASN org name from
the local MaxMind database. Requires data/GeoLite2-ASN.mmdb to exist.

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

    print("Fetching observations...")
    rows = conn.execute("""
        SELECT domain, checked_at, primary_ip, nameserver_provider_hint
        FROM observations
        WHERE primary_ip IS NOT NULL
    """).fetchall()
    print(f"  {len(rows):,} rows with a primary IP")

    updates: list[tuple[str | None, str, str]] = []

    with geoip2.database.Reader(str(GEOIP_PATH)) as reader:
        for domain, checked_at, ip, current_hint in rows:
            try:
                record = reader.asn(ip)
                new_hint = record.autonomous_system_organization
            except (geoip2.errors.AddressNotFoundError, Exception):
                new_hint = None

            if new_hint != current_hint:
                updates.append((new_hint, domain, checked_at))

    print(f"  {len(updates):,} rows would change")

    if dry_run:
        print("\nDry run — no changes written.")
        lookup = {(r[0], r[1]): r[3] for r in rows}
        print("\nSample updates (first 20):")
        for new_hint, domain, checked_at in updates[:20]:
            old = lookup.get((domain, checked_at), "—")
            print(f"  {domain}: {old!r} -> {new_hint!r}")
        return

    print("Writing updates...")
    conn.execute("BEGIN")
    try:
        conn.executemany(
            "UPDATE observations SET nameserver_provider_hint = ? WHERE domain = ? AND checked_at = ?",
            updates,
        )
        conn.execute("COMMIT")
        print(f"Done. {len(updates):,} rows updated.")
    except Exception as e:
        conn.execute("ROLLBACK")
        print(f"Error — rolled back: {e}")
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="outputs/observations.duckdb")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    backfill(args.db, args.dry_run)


if __name__ == "__main__":
    main()
