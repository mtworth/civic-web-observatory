# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Civic Web Index (package name `pwo` / "public-web-observatory") — an async crawler and analysis platform that maps digital infrastructure (availability, HTTPS/TLS, DNS/hosting, machine-readable files, static accessibility, tech fingerprinting) for .gov agencies and nonprofits. Homepage-only checks, no recursive crawling, no bot-protection bypass. See `concept.md` for the original MVP build spec and `README.md` for full operational docs (crawl options, API endpoints, database views, block-type taxonomy).

## Commands

```bash
uv sync                          # install deps (Python 3.11+, uv-managed)
cp .env.example .env             # then fill in MOTHERDUCK_TOKEN or leave blank for local DuckDB

uv run pwo crawl data/seed_domains.csv --limit 100 --concurrency 15   # crawl from a CSV
uv run pwo dotgov --limit 500 --concurrency 15                       # crawl .gov domains directly (no CSV needed)
uv run pwo serve                                                      # FastAPI server at :8000
uv run pwo download-geoip                                            # fetch MaxMind GeoLite2-ASN db (needs MAXMIND_LICENSE_KEY)
```

There is no test suite in this repo currently (no `tests/` directory despite being referenced in `concept.md`) and no configured lint/format command — check for one before assuming `ruff`/`black`/`pytest` conventions.

## Architecture

**Data flow:** CSV of domains → `crawler.py` (async orchestrator, per-domain concurrency) → parallel checks (`dns_checks`, `http_checks`, `tls_checks`, `machine_files`, `html_checks`, `hosting_hints`, `accessibility_static`, `detector`) → `status.py` maps results to a `collection_status` string → `models.DomainObservation` (Pydantic — single source of truth for the observation schema) → `db.py` inserts into DuckDB.

**Storage:** single `observations` table, one row per domain per crawl run (append-only history). `db.py` owns schema/migrations/views. Two backends, selected automatically based on env:
- Local DuckDB file (`PWO_DB_PATH`, default `outputs/observations.duckdb`) — fine for reads, but only one crawl process should write at a time.
- MotherDuck (`MOTHERDUCK_TOKEN` set → connects to `md:pwo`) — required for concurrent writers (e.g. CI + local).

Key views: `v_current` (latest snapshot per domain — what the API and dashboard read), `v_summary`, `v_https_adoption`, `v_hosting_breakdown`, `v_blocked`, `v_file_availability`, `v_tls_expiring`.

**API (`api.py`):** FastAPI app, opens a per-request DuckDB connection, all read logic lives in `queries.py`. `/api/stats` is the single consolidated endpoint the dashboard uses; the individual `/api/stats/*` endpoints are kept only for backwards compatibility.

**Frontend:** single static page at `pwo/static/index.html` served by the FastAPI app (Overview / Browse / Insights / Methods tabs) — no separate frontend build step.

**Technology fingerprinting (`detector.py`):** patterns sourced from enthec/webappanalyzer, bundled as 27 JSON files in `pwo/fingerprints/`, pre-compiled to regex at import time. Matches HTML body, headers, `<meta>`, `<script src>`, then expands `implies` relationships (e.g. WordPress → PHP → MySQL). Regenerating/updating fingerprints means re-vendoring from that upstream repo, not hand-editing.

**Blocking detection (`http_checks.py` + `status.py`):** classifies WAF/CDN blocks (Cloudflare, Akamai, Imperva, Sucuri, generic captcha/bot-challenge/rate-limit/reset) from headers and body markers, optionally retries once with a browser User-Agent. Never add evasion beyond this single documented UA retry — the project's ethics stance (see `concept.md`) explicitly forbids proxy rotation, fake fingerprints, CAPTCHA solving, and aggressive retries.

**Seed dataset generation:** `extract_990_domains.py` streams ~42 IRS 990 XML ZIPs from Cloudflare R2 (handles Deflate64, dedupes by EIN keeping most recent filing) → `nonprofit_domains.csv`; `dotgov.py` fetches the live CISA .gov list; `build_seed.py` merges both into `seed_domains.csv` (~318k rows). `data/seed_domains.csv` is the committed snapshot, regenerated every few months.

**Scheduled crawl (`.github/workflows/crawl.yml`):** daily GitHub Action slices the seed deterministically via `SHA-256(domain) % 90 == today.toordinal() % 90` so every domain gets crawled once per 90-day rotation window with no stored state. Requires `MOTHERDUCK_TOKEN` and `MAXMIND_LICENSE_KEY` as repo secrets.

## Working conventions

- `models.DomainObservation` is the schema of record — when adding a new signal/field, add it there first, then thread it through the relevant check module, `crawler.py`, and `db.py`'s insert/migration logic.
- Keep raw observations, not derived scores — this is a stated design principle (`concept.md`): store `homepage_status_code = 200`, not `public_web_score = 87`.
- Every domain must produce an observation row even on failure; a single domain's exception must never abort the whole crawl run.
