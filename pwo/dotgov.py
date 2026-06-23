import csv
import io
import random

import httpx

DOTGOV_CSV_URL = (
    "https://raw.githubusercontent.com/cisagov/dotgov-data/main/current-full.csv"
)

# Canonical column names in the dotgov CSV (lowercase, stripped)
_COL_DOMAIN = "domain name"
_COL_TYPE = "domain type"
_COL_AGENCY = "agency"
_COL_ORG = "organization"
_COL_STATE = "state"


def _normalize_cols(row: dict) -> dict:
    return {k.lower().strip(): v for k, v in row.items()}


def fetch_dotgov_domains(
    limit: int | None = None,
    seed: int = 42,
    domain_types: list[str] | None = None,
    sector: str | None = None,
) -> list[dict]:
    with httpx.Client(timeout=30) as client:
        resp = client.get(DOTGOV_CSV_URL)
        resp.raise_for_status()

    reader = csv.DictReader(io.StringIO(resp.text))
    rows = [_normalize_cols(r) for r in reader]

    if domain_types:
        types_lower = [t.lower() for t in domain_types]
        rows = [r for r in rows if r.get(_COL_TYPE, "").lower() in types_lower]

    if limit and limit < len(rows):
        rng = random.Random(seed)
        rows = rng.sample(rows, limit)

    result = []
    for row in rows:
        raw_domain = row.get(_COL_DOMAIN, "").strip().lower()
        if not raw_domain:
            continue
        # Strip any protocol prefix if accidentally present
        domain = raw_domain.removeprefix("https://").removeprefix("http://").rstrip("/")

        org = row.get(_COL_ORG, "").strip() or row.get(_COL_AGENCY, "").strip()
        result.append({
            "domain": domain,
            "source": "gsa_dotgov",
            "org_name": org or None,
            "org_type": row.get(_COL_TYPE, "").strip().lower() or None,
            "state": row.get(_COL_STATE, "").strip() or None,
            "ein": None,
            "sector": sector,
        })

    return result
