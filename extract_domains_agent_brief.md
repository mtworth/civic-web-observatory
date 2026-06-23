# Agent Brief: Extract Domains from IRS 990 XML Bulk Data

## Goal

Read IRS 990 XML filings for San Francisco nonprofits from Cloudflare R2 and extract each org's website domain. Output: a mapping of EIN → domain.

---

## Data in R2

**Bucket:** `npindex-990`  
**Endpoint:** `https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com`  
**Auth:** S3-compatible (AWS Signature V4), credentials in `.env`:
- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`

**Bucket contents (42 ZIPs total):**

| Prefix | Files |
|--------|-------|
| `2022/` | `index_2022.csv`, `2022_TEOS_XML_01A.zip`, `2022_TEOS_XML_02A.zip` |
| `2023/` | `index_2023.csv`, `2023_TEOS_XML_01A.zip` … `2023_TEOS_XML_12A.zip` |
| `2024/` | `index_2024.csv`, `2024_TEOS_XML_01A.zip` … `2024_TEOS_XML_12A.zip` |
| `2025/` | `index_2025.csv`, `2025_TEOS_XML_01A.zip` … `2025_TEOS_XML_12A.zip` (including `05B`, `11B`, `11C`, `11D` sub-batches) |
| `bmf/` | `eo_ca.csv` — IRS Business Master File for California |

The ZIPs use **Deflate64** compression. The stdlib `zipfile` module does not support this. Install and import `zipfile_deflate64` before importing `zipfile` — it monkey-patches stdlib automatically:
```python
import zipfile_deflate64  # noqa — patches stdlib zipfile
import zipfile
```

---

## Three-Step Process

### Step 1 — Identify SF nonprofits from the BMF

Download `bmf/eo_ca.csv` from R2. It is latin-1 encoded. Parse it and collect EINs where:
- `CITY == "SAN FRANCISCO"` (case-insensitive)
- `STATUS in ("1", "01")` (active orgs only)

Zero-pad EINs to 9 digits: `ein.strip().zfill(9)`.

This gives you the set of SF EINs (~5,000 orgs) to target.

### Step 2 — Find relevant filings via index CSVs

Each year has an `index_{year}.csv` in R2. Download all four (`2022`–`2025`). They are UTF-8 CSV files with at minimum these columns:

| Column | Description |
|--------|-------------|
| `OBJECT_ID` | Unique filing identifier |
| `EIN` | Employer Identification Number |
| `TAXPAYER_NAME` | Org name |
| `RETURN_TYPE` | `990`, `990EZ`, or `990PF` |
| `XML_BATCH_ID` | Which ZIP file contains this filing (e.g. `2023_TEOS_XML_05A`) |

Cross-reference `EIN` against your SF EIN set to get the subset of SF filings. Keep the most recent filing per EIN (highest `OBJECT_ID` or latest year) — that's the best source for a current domain.

**`XML_BATCH_ID` → ZIP key mapping:**
```
batch_id = "2023_TEOS_XML_05A"
zip_key  = f"{batch_id[:4]}/{batch_id.upper()}.zip"
# → "2023/2023_TEOS_XML_05A.zip"
```

### Step 3 — Extract domains from XML files

For each batch that contains at least one SF filing:

1. Download the ZIP from R2 into memory (`io.BytesIO`).
2. Open with `zipfile.ZipFile`. Each entry inside is named `BATCHID/OBJECTID_public.xml`.
3. For each XML file whose `OBJECT_ID` (the filename stem before `_public.xml`) matches a needed filing:
   - Parse with `xml.etree.ElementTree`.
   - Strip XML namespaces from all tags (namespaces vary by filing year): `re.sub(r"\{[^}]+\}", "", tag)`.
   - Extract domain from whichever path exists:

```python
DOMAIN_PATHS = [
    "./ReturnData/IRS990/WebsiteAddressTxt",          # full 990
    "./ReturnData/IRS990EZ/WebsiteAddressTxt",        # 990EZ
    "./ReturnData/IRS990PF/StatementsRegardingActyGrp/WebsiteAddressTxt",  # 990PF
]
```

4. Normalize the raw value:
```python
import re

def normalize_domain(url):
    url = url.strip()
    url = re.sub(r"^https?://", "", url, flags=re.IGNORECASE)
    url = url.rstrip("/").lower()
    return url if url not in ("", "n/a", "none", "www") else None
```

5. Once all needed `OBJECT_ID`s in a batch are found, stop reading the ZIP early.

---

## Output

A dict (or CSV) of:
```
EIN (9-digit zero-padded) → domain (bare, e.g. "example.org") or None
```

When an org has multiple filings across years, prefer the most recent non-null domain.

---

## Dependencies

```
boto3
python-dotenv
zipfile_deflate64
```

---

## Notes

- ZIPs are large (100 MB – 2.6 GB). Stream into `io.BytesIO` rather than writing to disk.
- Not all orgs list a website. Expect ~30–50% of SF orgs to have a non-null domain.
- `990PF` = private foundation; `990EZ` = small org. All three form types can have a domain field.
- The BMF `eo_ca.csv` is latin-1 encoded; the index CSVs are UTF-8.
- Filings within a ZIP are not in any guaranteed order — scan all entries and match by `OBJECT_ID`.
