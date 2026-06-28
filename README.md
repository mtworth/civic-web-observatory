# Public Web Observatory

A crawler and analysis platform that maps the digital infrastructure of the American public web — .gov agencies, nonprofits, and civic institutions. Each run produces a structured snapshot per domain covering availability, HTTPS/TLS posture, DNS/hosting, machine-readable files, static accessibility signals, and detected technologies.

Live database: ~19,000 observations across 17,000 domains, growing via periodic crawls.

---

## What it measures

Each domain gets one row per crawl run (`observations` table). The `v_current` view deduplicates to the latest snapshot per domain.

| Category | Fields |
|---|---|
| **Identity** | domain, org_name, org_type, state, EIN, sector, source |
| **DNS** | resolves, A/AAAA/CNAME/NS records |
| **Homepage** | status code, content type, redirect count, response time, final URL |
| **Blocking** | block_type (none / cloudflare_block / cloudflare_challenge / akamai_block / imperva_block / sucuri_block / captcha / bot_challenge / forbidden / rate_limited / connection_reset), UA retry result |
| **HTTPS / TLS** | available, redirects, valid cert, issuer, expiry |
| **Machine-readable files** | robots.txt, sitemap.xml, llms.txt (status, size, content signals) |
| **HTML signals** | title, meta description, html lang, canonical URL, mobile viewport, H1 count |
| **Hosting** | primary IP, ASN, ASN org, nameserver provider hint, CNAME hint |
| **Static accessibility** | missing alt text, unlabeled inputs, landmark presence |
| **Technologies** | 7,646 patterns via enthec/webappanalyzer (WordPress, Cloudflare, Bootstrap, etc.) |

---

## Project structure

```
pwo/
  api.py              FastAPI app — all read endpoints, per-request DuckDB connections
  cli.py              Click CLI — `pwo crawl`, `pwo dotgov`, `pwo serve`
  crawler.py          Async orchestrator — runs all checks per domain concurrently
  models.py           Pydantic DomainObservation (single source of truth for schema)
  db.py               DuckDB schema, migrations, views, insert/summary helpers
  queries.py          All read queries used by the API
  config.py           Config dataclass (concurrency, timeout, db_path, etc.)

  http_checks.py      Homepage fetch, redirect following, WAF/block detection, UA retry
  dns_checks.py       DNS resolution, A/AAAA/CNAME/NS records
  tls_checks.py       TLS certificate inspection
  machine_files.py    robots.txt, sitemap.xml, llms.txt fetches
  html_checks.py      HTML parsing — title, meta, viewport, H1, canonical
  hosting_hints.py    ASN lookup, nameserver/CNAME provider pattern matching
  accessibility_static.py  Static HTML accessibility audit (no browser required)
  detector.py         Technology fingerprinting (pre-compiled regex against html/headers/meta)
  status.py           Maps check results → collection_status string

  build_seed.py       Builds outputs/seed_domains.csv from CISA .gov + IRS 990 nonprofits
  extract_990_domains.py  Streams IRS 990 ZIPs from Cloudflare R2, extracts WebsiteAddressTxt
  dotgov.py           Fetches live CISA .gov domain list

  fingerprints/       27 JSON files from enthec/webappanalyzer (7,646 technology patterns)
  static/
    index.html        Single-page frontend (Overview / Browse / Insights / Methods)
    llms.txt          Machine-readable site description
```

---

## Setup

**Requirements:** Python 3.11+, [uv](https://github.com/astral-sh/uv)

```bash
git clone https://github.com/mtworth/publicwebobservatory
cd publicwebobservatory
uv sync
cp .env.example .env
# Fill in MOTHERDUCK_TOKEN (or leave blank to use a local DuckDB file)
```

**.env variables:**

```
MOTHERDUCK_TOKEN=        # MotherDuck hosted DuckDB — get token at app.motherduck.com
PWO_DB_PATH=outputs/observations.duckdb   # used only when MOTHERDUCK_TOKEN is unset

# Only needed to re-extract nonprofit domains from IRS 990 filings
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=
```

---

## Running a crawl

`.env` is loaded automatically by the CLI.

**Crawl from the seed dataset (recommended):**

```bash
# Full seed: 318k domains (gov + nonprofits)
pwo crawl outputs/seed_domains.csv --concurrency 15 --limit 1000

# Random subset, e.g. 20% daily rotation by hash bucket:
python3 -c "
import csv, random, re

random.seed(42)

def is_clean(d):
    if not d or len(d) < 4 or '@' in d or ' ' in d or '.' not in d: return False
    if not re.match(r'^[a-z0-9][a-z0-9._-]*\.[a-z]{2,}$', d): return False
    return '/' not in d and ':' not in d

with open('outputs/seed_domains.csv') as f:
    rows = [r for r in csv.DictReader(f) if is_clean(r['domain'])]

sample = random.sample(rows, 1000)

with open('outputs/sample.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(sample)
" && pwo crawl outputs/sample.csv --concurrency 15
```

**Crawl .gov domains directly (no seed file needed):**

```bash
pwo dotgov --limit 500 --concurrency 15
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--concurrency` | 10 | Parallel domain workers |
| `--timeout` | 15 | Per-request timeout (seconds) |
| `--limit` | all | Cap number of domains |
| `--output-dir` | outputs/ | Local fallback DB location |

---

## Running the API server

```bash
pwo serve
# → http://localhost:8000
```

**Endpoints:**

| Endpoint | Description |
|---|---|
| `GET /` | Frontend SPA |
| `GET /api/health` | Version check |
| `GET /api/summary` | Most recent run metadata + dataset totals |
| `GET /api/stats` | **All dashboard stats in one request** — https, tls, files, a11y, status, hosting, blocking, technologies, states, orgTypes |
| `GET /api/domains` | Paginated domain list (filters: status, sector, org_type, state, search) |
| `GET /api/domains/{domain}` | Single domain latest snapshot |
| `GET /api/domains/{domain}/history` | All crawl snapshots for a domain |
| `GET /api/blocked` | All currently-blocked domains |
| `GET /api/runs` | Run summary table |
| `GET /api/stats/status` | (kept for compatibility) Collection status breakdown |
| `GET /api/stats/https` | (kept for compatibility) HTTPS adoption |
| `GET /api/stats/hosting` | (kept for compatibility) Nameserver provider breakdown |
| `GET /api/stats/tls` | (kept for compatibility) TLS stats |
| `GET /api/stats/files` | (kept for compatibility) robots/sitemap/llms.txt |
| `GET /api/stats/accessibility` | (kept for compatibility) Static a11y summary |
| `GET /api/stats/blocking` | (kept for compatibility) WAF block type breakdown |
| `GET /api/stats/technologies` | (kept for compatibility) Top 30 technologies |

---

## Rebuilding the seed dataset

The seed (`outputs/seed_domains.csv`) combines ~302k IRS nonprofit domains and ~16k CISA .gov domains. It's not committed (too large) but can be regenerated:

```bash
# Step 1: extract nonprofit domains from IRS 990 XML filings (requires R2 credentials)
python3 -m pwo.extract_990_domains
# → outputs/nonprofit_domains.csv (~300k rows)

# Step 2: merge with live .gov list and deduplicate
python3 -m pwo.build_seed
# → outputs/seed_domains.csv (~318k rows, columns: domain, source, org_name, org_type, state, ein, sector)
```

The extraction streams ~42 ZIP files from Cloudflare R2, handles Deflate64 compression, and deduplicates by EIN keeping the most recent filing.

---

## Database

**Schema:** Single `observations` table, one row per domain per crawl run. The `v_current` view returns the latest snapshot per domain for dashboard queries.

**Storage options:**

- **Local DuckDB** (default): `outputs/observations.duckdb`. Read-only concurrent access is safe; concurrent writes are not — run one crawl at a time.
- **MotherDuck** (recommended for multi-process/cloud use): set `MOTHERDUCK_TOKEN`. Both crawler and API connect to `md:pwo` automatically. MotherDuck handles concurrent access natively.

**Key views:**

| View | Purpose |
|---|---|
| `v_current` | Latest snapshot per domain |
| `v_summary` | Aggregate counts (ok/blocked/failed/https/tls/files) |
| `v_https_adoption` | HTTPS and redirect percentages |
| `v_hosting_breakdown` | Provider counts |
| `v_blocked` | All domains with a non-null block type |
| `v_file_availability` | robots/sitemap/llms.txt counts |
| `v_tls_expiring` | Domains with certs expiring in <30 days |

---

## Technology fingerprinting

Patterns are from [enthec/webappanalyzer](https://github.com/enthec/webappanalyzer) (27 category files, 7,646 technologies). They're bundled in `pwo/fingerprints/` and pre-compiled at import time. The detector matches against HTML body, response headers, `<meta>` tags, and `<script src>` attributes, then expands `implies` relationships (e.g. WordPress → PHP → MySQL).

---

## Blocking detection

When the initial fetch is blocked, the crawler classifies it and optionally retries with a browser User-Agent (Chrome/124):

| Block type | Detection signal |
|---|---|
| `cloudflare_block` | CF-Ray header + 403/503 |
| `cloudflare_challenge` | `cf_chl_opt` in body |
| `akamai_block` | AkamaiGHost server header |
| `imperva_block` | `_Incapsula_` cookie or body marker |
| `sucuri_block` | Sucuri/CloudProxy server header |
| `captcha` | hCaptcha / reCAPTCHA body markers |
| `bot_challenge` | Generic bot challenge page signals |
| `forbidden` | 403 with no WAF fingerprint |
| `rate_limited` | 429 |
| `connection_reset` | TCP reset (errno 54/104) |

---

## Scheduled crawls (GitHub Actions)

The workflow at `.github/workflows/crawl.yml` runs daily at 6am UTC and crawls ~10,600 domains — one deterministic 1/30th slice of the seed dataset. Every domain is visited once per 30-day window.

**Sampling strategy:** `SHA-256(domain) % 30 == today.toordinal() % 30` — stable across Python versions, even distribution (10,453–10,833 domains/bucket), no state required.

**Setup:**

1. Add `MOTHERDUCK_TOKEN` as a repository secret (Settings → Secrets → Actions)
2. Enable Actions on the repo if not already on
3. The first scheduled run will fire at 6am UTC the next day

**Manual trigger:** Go to Actions → Daily Crawl → Run workflow. You can optionally override the bucket (0–29) to re-crawl a specific day's slice, or enable dry run to just print the domain count.

**Runtime:** ~45–65 minutes/day at concurrency 25. Uses ~1,350–1,950 of GitHub's 2,000 free minutes/month (public repo).

**Refreshing the seed:** `data/seed_domains.csv` should be regenerated every few months as new IRS 990 filings are published:

```bash
python3 -m pwo.extract_990_domains   # → outputs/nonprofit_domains.csv
python3 -m pwo.build_seed            # → outputs/seed_domains.csv
cp outputs/seed_domains.csv data/seed_domains.csv
git add data/seed_domains.csv && git commit -m "refresh seed dataset"
```

---

## Planned / next steps

- GitHub Actions workflow for daily 20% rotation crawl (~63k domains/day)
- Public deployment (Cloud Run or VPS) for the API
- `metadata JSON` column for experimental per-domain signals
- Sector-level aggregations in the Insights tab
