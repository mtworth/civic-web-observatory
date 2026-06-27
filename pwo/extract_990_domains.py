"""
Extract website domains from IRS 990 XML filings stored in Cloudflare R2.
Produces outputs/nonprofit_domains.csv with columns:
    domain, source, org_name, org_type, ein

Covers all nonprofits across all years (2022-2025), no geographic filter.
"""

import zipfile_deflate64  # noqa — monkey-patches stdlib zipfile
import zipfile

import csv
import io
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import boto3
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
OUTPUT_FILE = REPO_ROOT / "outputs" / "nonprofit_domains.csv"

load_dotenv(ENV_FILE)

R2_ACCOUNT_ID = os.environ["R2_ACCOUNT_ID"]
R2_ACCESS_KEY_ID = os.environ["R2_ACCESS_KEY_ID"]
R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
R2_BUCKET = os.environ["R2_BUCKET"]
R2_ENDPOINT = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

YEARS = ["2022", "2023", "2024", "2025"]
VALID_RETURN_TYPES = {"990", "990EZ", "990PF"}

DOMAIN_PATHS = [
    "./ReturnData/IRS990/WebsiteAddressTxt",
    "./ReturnData/IRS990EZ/WebsiteAddressTxt",
    "./ReturnData/IRS990PF/StatementsRegardingActyGrp/WebsiteAddressTxt",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_s3():
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def download_object(s3, key: str) -> bytes:
    """Download an R2 object entirely into memory."""
    resp = s3.get_object(Bucket=R2_BUCKET, Key=key)
    return resp["Body"].read()


def strip_ns(tag: str) -> str:
    return re.sub(r"\{[^}]+\}", "", tag)


def normalize_domain(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    url = re.sub(r"^https?://", "", url, flags=re.IGNORECASE)
    url = url.rstrip("/").lower()
    # strip trailing path — keep only host
    url = url.split("/")[0].split("?")[0]
    bad = {"", "n/a", "none", "www", "n/a.", "na", "n.a."}
    return url if url not in bad else None


def extract_domain_from_xml(xml_bytes: bytes) -> str | None:
    """Parse one 990 XML and return the normalized domain, or None."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    def find_text(elem, path_parts):
        cur = elem
        for part in path_parts:
            found = None
            for child in cur:
                if strip_ns(child.tag) == part:
                    found = child
                    break
            if found is None:
                return None
            cur = found
        return cur.text

    for path in DOMAIN_PATHS:
        parts = [p for p in path.split("/") if p and p != "."]
        text = find_text(root, parts)
        if text:
            return normalize_domain(text)
    return None


# ---------------------------------------------------------------------------
# Step 1 — Load all filings from index CSVs (no geographic filter)
# ---------------------------------------------------------------------------


def load_filings(s3) -> dict[str, dict]:
    """
    Return {ein: {object_id, taxpayer_name, xml_batch_id, year}} for the
    most-recent filing per EIN across all years and all valid return types.
    """
    # best_filing[ein] = {object_id, taxpayer_name, xml_batch_id, year, ein}
    best_filing: dict[str, dict] = {}

    for year in YEARS:
        key = f"{year}/index_{year}.csv"
        print(f"Downloading {key}…", flush=True)
        try:
            data = download_object(s3, key)
        except Exception as e:
            print(f"  WARNING: could not download {key}: {e}")
            continue

        reader = csv.DictReader(io.StringIO(data.decode("utf-8")))
        year_new = 0
        for row in reader:
            return_type = (row.get("RETURN_TYPE") or "").strip()
            if return_type not in VALID_RETURN_TYPES:
                continue

            ein = (row.get("EIN") or "").strip().zfill(9)
            object_id = (row.get("OBJECT_ID") or "").strip()
            batch_id = (row.get("XML_BATCH_ID") or "").strip()
            taxpayer_name = (row.get("TAXPAYER_NAME") or "").strip()

            if not ein or not object_id or not batch_id:
                continue

            # Keep most recent: compare by year first, then OBJECT_ID (lexicographic OK since same length)
            existing = best_filing.get(ein)
            if existing is None or int(year) > int(existing["year"]) or (
                int(year) == int(existing["year"]) and object_id > existing["object_id"]
            ):
                best_filing[ein] = {
                    "object_id": object_id,
                    "taxpayer_name": taxpayer_name,
                    "xml_batch_id": batch_id,
                    "year": year,
                    "ein": ein,
                }
                year_new += 1

        year_total = sum(1 for f in best_filing.values() if f["year"] == year)
        print(f"  Year {year}: {year_total:,} EINs now have this year as most-recent")

    total = len(best_filing)
    print(f"Total unique EINs (most-recent filing each): {total:,}")
    return best_filing


# ---------------------------------------------------------------------------
# Step 2 — Extract domains from ZIP files
# ---------------------------------------------------------------------------


def batch_id_to_zip_key(batch_id: str) -> str:
    year = batch_id[:4]
    return f"{year}/{batch_id.upper()}.zip"


def extract_domains_from_batches(s3, filings: dict) -> dict[str, str | None]:
    """
    Return {ein: domain_or_None} for every filing in `filings`.
    Streams each ZIP into memory and exits early once all needed entries are found.
    """
    # Group filings by batch
    batches: dict[str, list[dict]] = defaultdict(list)
    for filing in filings.values():
        batches[filing["xml_batch_id"]].append(filing)

    ein_to_domain: dict[str, str | None] = {}

    batch_list = sorted(batches.keys())
    print(f"\nProcessing {len(batch_list)} ZIP batches…", flush=True)

    for i, batch_id in enumerate(batch_list, 1):
        batch_filings = batches[batch_id]
        zip_key = batch_id_to_zip_key(batch_id)
        # Build lookup: object_id -> filing dict
        needed: dict[str, dict] = {f["object_id"]: f for f in batch_filings}

        print(f"  [{i}/{len(batch_list)}] {zip_key} ({len(needed)} filings needed)…", flush=True)

        try:
            zip_bytes = download_object(s3, zip_key)
        except Exception as e:
            print(f"    WARNING: could not download {zip_key}: {e}")
            for f in batch_filings:
                ein_to_domain[f["ein"]] = None
            continue

        remaining = set(needed.keys())
        found_count = 0

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for entry in zf.infolist():
                if not remaining:
                    break  # early exit once all needed EINs in this batch matched
                name = entry.filename
                # Filename pattern: BATCHID/OBJECTID_public.xml
                stem = name.split("/")[-1]
                if not stem.endswith("_public.xml"):
                    continue
                obj_id = stem[: -len("_public.xml")]
                if obj_id not in remaining:
                    continue

                filing = needed[obj_id]
                try:
                    xml_bytes_entry = zf.read(entry)
                except Exception as e:
                    print(f"    WARNING: could not read {name}: {e}")
                    ein_to_domain[filing["ein"]] = None
                    remaining.discard(obj_id)
                    continue

                domain = extract_domain_from_xml(xml_bytes_entry)
                ein_to_domain[filing["ein"]] = domain
                remaining.discard(obj_id)
                if domain:
                    found_count += 1

        # Any object_ids not found in the ZIP
        for obj_id in remaining:
            filing = needed[obj_id]
            ein_to_domain[filing["ein"]] = None

        total_in_batch = len(batch_filings)
        extracted = found_count
        print(f"    Batch {batch_id}: {total_in_batch - len(remaining)}/{total_in_batch} found, {extracted} domains extracted")

    return ein_to_domain


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    s3 = get_s3()

    # Step 1: All filings (no SF filter)
    filings = load_filings(s3)

    # Step 2: Extract domains from XMLs
    ein_to_domain = extract_domains_from_batches(s3, filings)

    # Build output rows (only rows with a non-null domain)
    rows = []
    for ein, filing in filings.items():
        domain = ein_to_domain.get(ein)
        if not domain:
            continue
        rows.append({
            "domain": domain,
            "source": "irs_990",
            "org_name": filing["taxpayer_name"],
            "org_type": "nonprofit",
            "ein": ein,
            "_year": filing["year"],  # internal, for dedup
        })

    # Deduplicate by domain: if multiple EINs share a domain, keep the one
    # with the most recent filing year
    seen_domain: dict[str, dict] = {}
    for row in rows:
        d = row["domain"]
        if d not in seen_domain:
            seen_domain[d] = row
        else:
            if row["_year"] > seen_domain[d]["_year"]:
                seen_domain[d] = row

    deduped = sorted(seen_domain.values(), key=lambda r: r["domain"])

    # Write output (drop internal _year field)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["domain", "source", "org_name", "org_type", "ein"]
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(deduped)

    # Summary
    total_eins = len(filings)
    total_with_domain = sum(1 for v in ein_to_domain.values() if v is not None)
    total_unique_domains = len(deduped)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total unique EINs across all filings:  {total_eins:,}")
    print(f"Total with a non-null domain:          {total_with_domain:,}")
    print(f"Total unique domains in output:        {total_unique_domains:,}")
    print(f"Output written to: {OUTPUT_FILE}")
    print()
    print("First 15 rows:")
    print("-" * 60)
    print(",".join(fieldnames))
    for row in deduped[:15]:
        print(",".join(str(row.get(f, "")) for f in fieldnames))


if __name__ == "__main__":
    main()
