"""
Build the canonical seed domain dataset.

Sources:
  1. CISA .gov registry (live from GitHub)
  2. outputs/nonprofit_domains.csv (extracted from IRS 990 filings)

Output:
  outputs/seed_domains.csv  — deduplicated, cleaned, sector-tagged

Usage:
  .venv/bin/python -m pwo.build_seed
"""

from __future__ import annotations

import csv
import io
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent
OUTPUT = ROOT / "outputs" / "seed_domains.csv"
NONPROFIT_CSV = ROOT / "outputs" / "nonprofit_domains.csv"

CISA_URL = "https://raw.githubusercontent.com/cisagov/dotgov-data/main/current-full.csv"

INVALID_DOMAINS = {
    "", "n/a", "na", "none", "www", "tbd", "unknown", "website",
    "coming soon", "under construction", "http", "https",
}


def normalize(raw: str) -> str | None:
    d = raw.strip()
    d = re.sub(r"^https?://", "", d, flags=re.IGNORECASE)
    d = re.sub(r"/.*$", "", d)           # strip path
    d = d.strip().lower()
    d = d.lstrip("@")                    # some values start with @
    if " " in d:                         # spaces inside = not a valid domain
        return None
    if d in INVALID_DOMAINS:
        return None
    if "." not in d or len(d) < 4:
        return None
    return d


def load_gov() -> list[dict]:
    print("Fetching CISA .gov registry…", flush=True)
    r = httpx.get(CISA_URL, timeout=30, follow_redirects=True)
    r.raise_for_status()
    rows = []
    for rec in csv.DictReader(io.StringIO(r.text)):
        domain = normalize(rec.get("Domain name", ""))
        if not domain:
            continue
        rows.append({
            "domain": domain,
            "source": "cisa_dotgov",
            "org_name": rec.get("Organization name", "").strip() or None,
            "org_type": rec.get("Domain type", "").strip().lower() or None,
            "state": rec.get("State", "").strip() or None,
            "ein": None,
            "sector": "government",
        })
    print(f"  {len(rows):,} .gov domains loaded", flush=True)
    return rows


def load_nonprofits() -> list[dict]:
    if not NONPROFIT_CSV.exists():
        print(f"WARNING: {NONPROFIT_CSV} not found — skipping nonprofits", flush=True)
        return []
    print(f"Loading {NONPROFIT_CSV.name}…", flush=True)
    rows = []
    skipped = 0
    with NONPROFIT_CSV.open(encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            domain = normalize(rec.get("domain", ""))
            if not domain:
                skipped += 1
                continue
            rows.append({
                "domain": domain,
                "source": rec.get("source", "irs_990").strip(),
                "org_name": rec.get("org_name", "").strip() or None,
                "org_type": rec.get("org_type", "nonprofit").strip() or None,
                "state": rec.get("state", "").strip() or None,
                "ein": rec.get("ein", "").strip() or None,
                "sector": "nonprofit",
            })
    print(f"  {len(rows):,} nonprofit domains loaded ({skipped:,} skipped/invalid)", flush=True)
    return rows


def merge(gov: list[dict], nonprofits: list[dict]) -> list[dict]:
    """
    Merge .gov and nonprofit rows. .gov takes precedence on conflicts —
    if a domain appears in both, keep the .gov row (some nonprofits incorrectly
    list a .gov URL). Deduplicate within each source by domain.
    """
    seen: dict[str, dict] = {}

    # .gov first so they win on conflict
    for row in gov:
        seen[row["domain"]] = row

    # nonprofits fill in the rest
    for row in nonprofits:
        if row["domain"] not in seen:
            seen[row["domain"]] = row

    result = sorted(seen.values(), key=lambda r: r["domain"])
    return result


def write(rows: list[dict]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fields = ["domain", "source", "org_name", "org_type", "state", "ein", "sector"]
    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {len(rows):,} rows → {OUTPUT}", flush=True)


def main() -> None:
    gov = load_gov()
    nonprofits = load_nonprofits()
    merged = merge(gov, nonprofits)

    # Summary
    sectors = {}
    for r in merged:
        sectors[r["sector"]] = sectors.get(r["sector"], 0) + 1

    print(f"\n── Seed dataset summary ──")
    for sector, n in sorted(sectors.items(), key=lambda x: -x[1]):
        print(f"  {sector:<15} {n:>8,}")
    print(f"  {'TOTAL':<15} {len(merged):>8,}")

    write(merged)


if __name__ == "__main__":
    main()
