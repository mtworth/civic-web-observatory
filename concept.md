# Public Web Observatory — V1 MVP Build Spec

## Goal

Build a lightweight crawler and dataset generator for **Public Web Observatory V1**.

The MVP should evaluate the **homepage only** for a list of public-interest domains. For each domain, collect basic availability, machine-readability, hosting/DNS, and automated accessibility signals.

This is not a deep crawler. Do not recursively crawl websites. Do not bypass bot protection. Do not perform vulnerability scanning.

## Core Question

For each public-interest domain:

> Does the homepage load, use HTTPS, expose machine-readable discovery files, reveal basic hosting/DNS information, and pass basic automated accessibility checks?

## Inputs

Create a script that accepts a CSV of domains.

Example input file:

```csv
domain,source,org_name,org_type,state
berkeleyca.gov,gsa_gov,City of Berkeley,government,CA
example.org,irs_990,Example Nonprofit,nonprofit,CA
```

Required input fields:

```text
domain
```

Optional input fields:

```text
source
org_name
org_type
state
ein
```

## Outputs

Create one row per domain observation.

Output formats:

1. `outputs/observations.csv`
2. `outputs/observations.jsonl`

Also create:

```text
outputs/errors.jsonl
outputs/run_summary.json
```

## Crawler Rules

Use a transparent User-Agent.

```text
PublicWebObservatoryBot/0.1 (+https://publicwebobservatory.org/bot; contact: hello@publicwebobservatory.org)
```

Crawl only these URLs per domain:

```text
https://{domain}/
http://{domain}/
https://{domain}/robots.txt
https://{domain}/sitemap.xml
https://{domain}/llms.txt
```

Do not crawl deeper links.

Respect robots.txt for homepage fetch decisions when robots.txt is available and parseable.

If a site blocks the crawler, do not evade the block. Record the block.

Avoid:

```text
rotating proxies
fake browser fingerprints
CAPTCHA solving
login attempts
admin path probing
aggressive retries
pretending to be Googlebot
```

## Recommended Python Stack

Use:

```text
httpx
beautifulsoup4
lxml
dnspython
ipwhois
cryptography
playwright
axe-playwright-python
pandas
pydantic
tenacity
```

Use Playwright only for homepage axe scans.

Use `httpx` for the basic HTTP/file checks.

## Fields to Collect

### Identity Fields

```text
domain
source
org_name
org_type
state
ein
checked_at
crawler_version
```

### DNS Fields

```text
dns_resolves
dns_error
a_records
aaaa_records
cname_records
ns_records
```

### Homepage Availability Fields

```text
homepage_attempted
homepage_url
homepage_final_url
homepage_status_code
homepage_content_type
homepage_response_time_ms
homepage_size_bytes
homepage_redirect_count
homepage_error
homepage_block_type
```

`homepage_block_type` should use values like:

```text
none
forbidden
rate_limited
captcha
bot_challenge
cloudflare_block
access_denied
timeout
unknown
```

### HTTPS / TLS Fields

```text
https_available
http_available
http_redirects_to_https
tls_valid
tls_error
tls_issuer
tls_not_before
tls_not_after
tls_days_until_expiry
```

### Machine-Readable File Fields

For each of these:

```text
robots.txt
sitemap.xml
llms.txt
```

Collect:

```text
robots_txt_status_code
robots_txt_available
robots_txt_size_bytes
robots_txt_error

sitemap_xml_status_code
sitemap_xml_available
sitemap_xml_size_bytes
sitemap_xml_url_count
sitemap_xml_error

llms_txt_status_code
llms_txt_available
llms_txt_size_bytes
llms_txt_has_markdown_headings
llms_txt_link_count
llms_txt_error
```

Also parse robots.txt for:

```text
robots_allows_homepage
robots_sitemaps_listed
```

### Basic HTML Fields

From the homepage HTML:

```text
page_title
page_title_present
meta_description_present
html_lang_present
canonical_url
mobile_viewport_present
h1_count
```

### Hosting / Infrastructure Fields

Do not do full technology fingerprinting in V1.

Collect only basic hosting hints:

```text
primary_ip
host_asn
host_asn_org
host_network_name
nameserver_provider_hint
cname_provider_hint
```

Provider hints can be simple string matches on DNS/ASN evidence:

```text
Cloudflare
AWS
Azure
Google
Fastly
Akamai
GoDaddy
Pantheon
Acquia
WP Engine
Netlify
Vercel
GitHub Pages
unknown
```

### Static Accessibility Fields

Run these on the homepage HTML using BeautifulSoup:

```text
static_a11y_ran
static_a11y_error
a11y_has_title
a11y_has_html_lang
a11y_h1_count
a11y_images_total
a11y_images_missing_alt
a11y_inputs_total
a11y_inputs_missing_labels
a11y_has_main_landmark
a11y_has_nav_landmark
```

### Axe Accessibility Fields

Run axe only if:

```text
homepage_status_code == 200
content_type includes text/html
homepage is not blocked
homepage is not obviously parked
```

Use Playwright + axe.

Collect:

```text
axe_ran
axe_error
axe_violations_total
axe_violations_critical
axe_violations_serious
axe_violations_moderate
axe_violations_minor
axe_rule_ids
axe_wcag_tags
```

Store detailed axe violations in JSONL as nested data if practical.

### Parked / Placeholder Detection

Add simple detection for obvious parked or placeholder homepages.

Fields:

```text
is_probably_parked
parked_reason
```

Detect phrases like:

```text
domain for sale
buy this domain
parked free
coming soon
under construction
this domain is parked
website expired
account suspended
```

Also flag if final URL redirects to:

```text
facebook.com
linktr.ee
instagram.com
x.com
twitter.com
```

Use:

```text
social_only_redirect = true
social_only_platform = facebook / instagram / linktree / x / other
```

## Status Taxonomy

Each domain should get an overall `collection_status`.

Allowed values:

```text
ok
dns_failed
tls_failed
timeout
connection_refused
robots_disallowed
http_403
http_429
bot_challenge
captcha
blocked_by_cdn
too_many_redirects
non_html_homepage
parked_or_placeholder
parse_failed
unknown_error
```

## Implementation Structure

Create this project structure:

```text
public-web-observatory/
  README.md
  pyproject.toml
  .env.example
  data/
    input_domains.csv
  outputs/
    observations.csv
    observations.jsonl
    errors.jsonl
    run_summary.json
  pwo/
    __init__.py
    config.py
    models.py
    dns_checks.py
    http_checks.py
    tls_checks.py
    machine_files.py
    html_checks.py
    accessibility_static.py
    accessibility_axe.py
    hosting_hints.py
    status.py
    crawler.py
    cli.py
  tests/
    test_status.py
    test_html_checks.py
    test_hosting_hints.py
```

## CLI

Create a command like:

```bash
python -m pwo.cli crawl data/input_domains.csv --output-dir outputs --limit 100
```

Options:

```text
--limit
--concurrency
--skip-axe
--timeout
--output-dir
--user-agent
```

Defaults:

```text
concurrency = 10
timeout = 15 seconds
skip_axe = false
max_response_bytes = 500000
crawler_version = 0.1.0
```

## Rate Limiting

Use modest concurrency.

Rules:

```text
Global concurrency: 10 by default
Per-domain concurrency: 1
Retries: max 1 retry for transient timeout/connection errors
No retry for 403, 401, 429, CAPTCHA, or bot challenge
```

## Error Handling

Every domain should produce an observation row even if it fails.

Do not crash the full run because one domain fails.

Capture:

```text
error_type
error_message
stage
```

Stages:

```text
dns
http
tls
robots
sitemap
llms
html_parse
static_a11y
axe
hosting
```

## Data Model

Use Pydantic models for observations.

Example:

```python
class DomainObservation(BaseModel):
    domain: str
    source: str | None = None
    org_name: str | None = None
    org_type: str | None = None
    state: str | None = None
    ein: str | None = None

    checked_at: datetime
    crawler_version: str

    dns_resolves: bool | None = None
    dns_error: str | None = None
    a_records: list[str] = []
    aaaa_records: list[str] = []
    cname_records: list[str] = []
    ns_records: list[str] = []

    homepage_status_code: int | None = None
    homepage_final_url: str | None = None
    homepage_response_time_ms: int | None = None
    homepage_redirect_count: int | None = None
    homepage_error: str | None = None

    robots_txt_available: bool | None = None
    sitemap_xml_available: bool | None = None
    llms_txt_available: bool | None = None

    static_a11y_ran: bool = False
    axe_ran: bool = False

    collection_status: str
```

Expand as needed.

## Important Design Principle

Store raw observations first.

Do not create opaque scores in V1.

Good:

```text
homepage_status_code = 200
tls_valid = true
llms_txt_available = false
axe_violations_serious = 4
```

Avoid:

```text
public_web_score = 87
```

Scores can come later.

## Run Summary

At the end, create `run_summary.json` with:

```text
started_at
finished_at
duration_seconds
domains_total
domains_ok
domains_failed
domains_blocked
domains_with_robots_txt
domains_with_sitemap_xml
domains_with_llms_txt
domains_with_valid_tls
domains_with_axe_scan
average_response_time_ms
crawler_version
```

## README

Write a README explaining:

```text
What this project does
What it does not do
How to install dependencies
How to run a crawl
How to interpret outputs
Crawler ethics
User-Agent policy
Known limitations
```

Use this framing:

> Public Web Observatory performs lightweight, transparent, homepage-only checks of public-interest websites. It records availability, machine-readability, hosting/DNS hints, and automated accessibility signals. It does not bypass bot protections, crawl private areas, or perform vulnerability scanning.

## Acceptance Criteria

The project is complete when:

1. I can provide a CSV of domains.
2. The crawler checks homepage, robots.txt, sitemap.xml, and llms.txt.
3. The crawler records DNS, HTTPS, TLS, redirect, and status-code facts.
4. The crawler records basic hosting hints from DNS/IP/ASN.
5. The crawler runs static accessibility checks.
6. The crawler optionally runs axe homepage scans.
7. The crawler writes CSV, JSONL, errors JSONL, and run summary.
8. The crawler does not crash on failed domains.
9. The README explains how to run the project.
10. The implementation avoids bypassing bot protection.

