# site — the static Civic Web Observatory

The read side of Civic Web Observatory as **pure static files** — no FastAPI, no
Railway, no live DB connection — a Parquet snapshot queried in-browser with
[DuckDB-WASM](https://duckdb.org/docs/api/wasm/overview). This is what
`.github/workflows/deploy-pages.yml` publishes to GitHub Pages.

This started as an experiment (originally under `testmigration/`, renamed
here once the approach was validated) and does not touch `pwo/`'s FastAPI
app code — the two now share only `pwo/crawler.py`/`pwo/models.py`/
`pwo/parquet_writer.py` for ingestion (see "Ingestion" below).
`pwo/api.py` and `.github/workflows/crawl.yml` (the MotherDuck-writing
crawl) still exist but are no longer the production path — `crawl.yml`'s
schedule is disabled in favor of `crawl-static.yml`.

## What's here

- `export_data.py` — connects to MotherDuck and exports `v_current`,
  `v_summary`, `v_dashboard` to `data/*.parquet` (ZSTD-compressed). This
  is the "just dump today's live state" path, useful for a quick check.
- `partition_export.py` — connects to MotherDuck and splits `observations`
  into one small immutable Parquet file per crawl day, under
  `data/observations/<date>.parquet`. This simulates what a rewritten
  crawler would write directly (one partition per run) instead of
  inserting into a database — see "Designing for continuous crawling"
  below for why this is a separate file per day rather than one growing
  file.
- `compact.py` — **the MotherDuck-free path.** Reads only local files
  (`data/observations/*.parquet`, no DB connection, no token) and
  recomputes `data/v_current.parquet` with the exact same
  `QUALIFY ROW_NUMBER() OVER (PARTITION BY domain ORDER BY checked_at DESC) = 1`
  dedup logic as the `v_current` view in `pwo/db.py`. Verified to produce
  an identical result (227,446 domains, same stats) to exporting
  `v_current` straight from MotherDuck. Also writes `data/tech_top.json` —
  the default technology breakdown Explore's sidebar shows before any
  search, precomputed here instead of scanning live in the browser (that
  scan measured ~1.2s in DuckDB-WASM, more than every other query on the
  page combined — see "Performance" below).
- `example-crawl-workflow.yml` — a sketch of the real production workflow
  (crawl → write partition → commit to a `data` branch → compact →
  publish to a GitHub Release). **Not wired up** — lives outside
  `.github/workflows/` on purpose so it can't run; copy it in once the
  crawler itself is rewritten to emit partitions.
- `data/` — the exported Parquet files. **Gitignored** — regenerate with
  the scripts above rather than committing them.
- `static/style.css` — copied verbatim from `pwo/static/style.css`. Same
  visual design as production, not a reskin.
- `static/duckdb-client.js` — the shared DuckDB-WASM bootstrap every page
  imports: registers `data/v_current.parquet` as a `v_current` view (same
  name, same shape as the real view in `pwo/db.py`) and exports small
  helpers (`formatInt`, `timeAgo`, `signalFor`, `rowsToObjects`) mirroring
  the Jinja filters and `_signal_for`/`_time_ago` helpers in `pwo/api.py`.
- `index.html`, `explore.html`, `domain.html` — data pages, all reading
  `v_current.parquet` client-side via DuckDB-WASM instead of a server route:
  - **index.html** — hero domain count + the cycling "recent observations"
    feed (`USING SAMPLE 60`, mirrors `get_recent_observations`).
  - **explore.html** — a sidebar faceted-search layout: all six filter
    groups (technology, collection status, security, blocking, files,
    hosting) visible at once with counts, click to toggle, combinable
    across groups (AND'd together — e.g. technology=jQuery + status=ok
    narrows correctly). The technology group has a free-text search box
    that queries the *full* distinct technology set live (not a capped
    top-N), plus a scrollable list, so every technology is reachable, not
    just the most common ones. No state filter — dropped as not useful at
    this stage (the results table still shows state as a column).
  - **domain.html** — the field-grid single-domain profile plus a status
    strip (status/HTTPS/blocking/TLS-expiry at a glance), via `?d=<domain>`
    query param instead of a path segment, since there's no server to route
    227k+ domain paths to. Full observation history is intentionally not
    shown — see "What this does and doesn't prove" below.

  Plain multi-page navigation with full reloads between them, same
  philosophy as the production SSR app ("a page reload for a filter change
  is an acceptable, simple trade") — no client-side router.
- `insights.html`, `insights-post.html`, `insights/` — a Markdown-backed
  blog, replacing an earlier aggregate-stats dashboard version that turned
  out redundant with Explore's filters. Mirrors `pwo/reports.py`'s exact
  format (YAML-ish frontmatter block, `title`/`date`/`summary`, then a
  Markdown body) and parsing logic (`static/insights-client.js`), but reads
  static `.md` files client-side instead of a server-side glob:
  - `insights/manifest.json` lists post slugs — a static host can't glob a
    directory, so add a new post's slug here when adding
    `insights/posts/<slug>.md`.
  - `insights.html` is the post list (mirrors `reports.html`);
    `insights-post.html?slug=<slug>` is a single post (mirrors
    `report.html`), rendered with [marked](https://github.com/markedjs/marked)
    from CDN.
  - `insights/posts/welcome-to-the-blog.md` is a placeholder first post.

## Designing for continuous crawling

The crawl doesn't run once — it runs daily, forever, cycling through the
seed list on a 90-day rotation (~3,500 domains/day at current volume,
confirmed against the live data). Any GH-native replacement for
MotherDuck has to handle that accumulation without either (a) re-writing
a single ever-growing file into git history every day, which bloats the
repo since Parquet doesn't diff/delta well as a binary format, or
(b) losing state between runs.

The split used here:

1. **Raw partitions are immutable and small.** Each day's crawl produces
   its own `<date>.parquet` (~500KB at current volume) and only that file
   is ever written — previous days' files are never touched. Committing a
   new small file each day is ordinary git growth (like committing daily
   log files), not the "rewrite a 30MB+ blob every day" problem a single
   monolithic file would cause. These would live on a dedicated `data`
   branch to keep the daily bot commits out of `main`'s history.
2. **The compacted snapshot is mutable but never lives in git.** The
   static site doesn't need history — it needs "latest observation per
   domain," which stays roughly flat-sized as more days accumulate (it
   grows only with new *domains*, not new *observations*). Republishing
   that as a GitHub Release asset (`gh release upload latest
   v_current.parquet --clobber`) means it can be fully overwritten every
   day forever with zero git history cost, since release assets aren't
   part of the repo's git objects.
3. **The static site's deploy is decoupled from the data's refresh.**
   `index.html` fetches a stable release URL — GH Pages only needs to
   redeploy when the app code changes; the daily crawl updates the data
   independently by re-uploading to the same release tag.

Not addressed yet, worth revisiting after 6-12 months of continuous
crawling: the partition count itself grows unbounded (~365 tiny files/
year). At some point a periodic rollup (e.g. monthly) that merges old
daily partitions into fewer larger files would keep `git clone` fast.
Not urgent at 67 files today; will matter eventually.

## Run it locally

```bash
# 1. Get raw daily partitions into site/data/observations/ — either:
#    a) crawl straight to Parquet (the real path — no MotherDuck at all):
uv run pwo crawl data/seed_domains.csv --out-parquet site/data/observations/$(date +%F).parquet
#    b) or, for historical/backfill data still only in MotherDuck:
uv run python site/partition_export.py   # needs MOTHERDUCK_TOKEN in .env

# 2. Compact partitions into the flat snapshot the site reads — this step
#    is MotherDuck-free, local files only, and is what crawl-static.yml runs
uv run python site/compact.py

# 3. Serve the folder over HTTP (must be a real HTTP server, not file://,
#    since DuckDB-WASM fetches the Parquet file via range requests)
cd site
python3 -m http.server 8080

# 4. Open http://localhost:8080
```

(`export_data.py` still works too, for a quicker "just dump today's live
`v_current`" check — but `partition_export.py` + `compact.py` is the path
that proves out the MotherDuck-free design.)

Note: Python's built-in `http.server` has flaky `Range` header support.
DuckDB-WASM falls back to a full download when range requests aren't
honored, so it still works locally — it just won't demonstrate the
partial-fetch behavior you'd see on GH Pages / any CDN. If you want to
verify true range-request behavior locally, use `npx http-server -p 8080`
or `npx serve` instead.

## Deploying to GH Pages

**Live at https://mtworth.github.io/civic-web-observatory/** (repo made
public to get there — GitHub Pages and Release assets both require public
access to work for anonymous visitors; see the note on CORS below for the
other thing that wasn't obvious until deployed for real).

Two workflows, different triggers:

- **`deploy-pages.yml`** publishes `site/` to GitHub Pages — on push to
  `main` touching `site/**` (app code changed), on `workflow_dispatch`, or
  via `repository_dispatch` (`crawl-static.yml` fires this after
  publishing new data). Before packaging the artifact, it downloads
  `v_current.parquet`/`tech_top.json` from the `latest` Release into
  `site/data/` — **not** fetched cross-origin at runtime. GitHub's
  release-asset CDN sends no `Access-Control-Allow-Origin` header, so a
  browser blocks a cross-origin `fetch()`/XHR to it even though `curl`
  (which doesn't enforce CORS) succeeds against the exact same URL — this
  was only caught by testing the actual deployed site, not by testing the
  URL directly. Baking the file into the same-origin deploy artifact
  sidesteps it entirely.
- **`crawl-static.yml`** writes today's partition, commits it to the
  `data` branch, runs `compact.py`, publishes the new snapshot to the
  `latest` Release, then dispatches `deploy-pages.yml` so that data
  actually reaches the live site the same day.

## Ingestion — the crawler itself, MotherDuck-free

`pwo/crawler.py` was already well-decoupled from storage: `run_crawl()`
returns plain `DomainObservation` objects and only calls an `on_result`
callback per result — it never talks to a database itself. All DB writing
happened in `cli.py`'s `_run()`. That meant adding a MotherDuck-free output
path didn't require touching the crawler at all:

- **`pwo/parquet_writer.py`** (new) — `write_observations_parquet()`
  writes a batch of `DomainObservation`s straight to a Parquet file. No DB
  connection, no `MOTHERDUCK_TOKEN`. DuckDB's Python replacement scan
  doesn't accept a plain list of dicts (only DataFrame/Arrow/relations),
  and this project has no pandas/pyarrow dependency, so it round-trips
  through a temp NDJSON file and lets DuckDB's JSON reader do the
  materialization — but every column is explicitly cast to the type
  derived from `DomainObservation`'s own field annotations, not left to
  `read_json_auto()`'s inference. That distinction mattered in testing: a
  3-domain batch where every row had `org_name = None` got inferred as
  JSON type instead of VARCHAR, which then broke `compact.py`'s glob read
  the moment that partition sat next to a differently-typed one. Explicit
  casts make every partition's schema identical regardless of which
  columns happen to be all-NULL in a given day's batch.
- **`cli.py`** — `pwo crawl`/`pwo dotgov` gained a `--out-parquet PATH`
  flag. Omit it and behavior is unchanged (writes to MotherDuck/DuckDB) —
  `.github/workflows/crawl.yml` still has that code path available via
  manual `workflow_dispatch`, though its schedule is now disabled in favor
  of `crawl-static.yml`. Pass `--out-parquet` and the command never opens
  a DB connection at all: results are collected in memory and written
  once at the end.
- **`compact.py`** — now takes `--partitions-dir`/`--out-dir` (defaulting
  to the same local paths as before, so plain `uv run python
  site/compact.py` is unchanged) so `crawl-static.yml` can point it
  at a checked-out `data` branch. Also switched to
  `read_parquet(glob, union_by_name=true)` plus an explicit `EXCLUDE` of
  nine legacy `axe_*` columns — the historical MotherDuck-sourced
  partitions carry those from before that code was removed
  (`chore: remove stale files and axe stub code`), but partitions written
  by the new `--out-parquet` path don't have them, and DuckDB's default
  glob read requires byte-identical schemas across every file it globs.

Verified end-to-end, not just written: ran a real `pwo dotgov --limit 3
--out-parquet` crawl, copied the resulting partition alongside the 67 real
historical ones, ran `compact.py`, and confirmed the 3 domains it crawled
correctly took precedence (latest `checked_at` wins the per-domain
dedup), the total stayed at 227,446 (no duplicates), and the static site
still worked against the result with zero console errors. Also ran a
`pwo crawl` (not just `dotgov`) against a one-row CSV to confirm the
command production's actual crawl.yml uses works the same way, including
the failure path (a DNS-failed domain still writes a valid row).

See "Deploying to GH Pages" above for the workflows that run this for
real. `.github/workflows/crawl.yml` (the old MotherDuck-writing crawl)
still exists as a manual `workflow_dispatch` fallback, but its schedule
is disabled — `crawl-static.yml` is the production path now.

## What this does and doesn't prove

**In scope for this prototype:**
- Loading a ~28MB Parquet file (227k domains, 95 columns) in-browser and
  querying it with real SQL, no server.
- Home, Explore, and a domain profile page, mirroring the real
  `home.html`/`explore.html`/`domain.html` templates' markup, CSS, and
  behavior — facet browsing (6 categories), search/entity-type/state
  filters combined with a facet, pagination, the domain inspector side
  panel, and the recent-observations feed — all reimplemented as
  client-side SQL instead of Jinja + `/api/*`.
- Verified against real interaction, not just "it loads": combined
  facet + state + search filtering narrows results correctly
  (227,446 → 105,227 with a technology facet → 24 adding a state → 7
  adding a search term → back to 227,446 on clear), and a request-token
  guard was added to `loadRows()` after testing surfaced a real race
  condition where an in-flight unfiltered query could resolve after a
  filtered one and silently clobber it.

**Deliberately out of scope for now** (see the CLAUDE.md discussion this
came out of):
- Full observation history (`observations` table, growing ~4x/year as the
  90-day rotation repeats) — only the latest snapshot (`v_current`) is
  shipped. `domain.html` shows the latest observation only, with a note
  in the UI saying so. Per-domain history would need its own decision:
  ship it too, page it, or drop it from the static version for good.
- Per-domain static pages / SEO — `domain.html?d=<domain>` is a client
  rendered query-param page, not a real path; a crawler hitting
  `/domains/some-agency.gov` directly gets nothing without JS execution.
  If that matters, static pre-rendering per domain (227k+ pages now) is a
  separate build step to design.
- Reports (`reports/posts/*.md`) — not touched here; those are already
  static-friendly and would likely prerender to HTML separately.
- `/api/*` JSON endpoints for external consumers — no equivalent yet;
  could just be "fetch the Parquet file yourself" going forward, or a
  documented DuckDB-WASM snippet.

## Performance

Explore's initial page load fires ~10 queries against `v_current` (total
count, org-type breakdown, 6 sidebar facet groups, the table's count +
row select) — all on one DuckDB-WASM connection, which serializes them
(single-threaded WASM without cross-origin-isolation headers to enable
the multi-threaded build; opening more JS-level connections doesn't
change that). Measured directly (`conn.query` timed on an already-warm
connection): most of those queries run in 16–120ms, except the
technology sidebar group — a `GROUP BY` after `UNNEST()`-ing the
`technologies` list column across all 227k rows — which took **~1.2s**,
more than every other query on the page combined.

Fixed by precomputing that one query's *default* (no search term) result
in `compact.py` and shipping it as `data/tech_top.json`, fetched instead
of queried on page load. A typed search still runs the live UNNEST query
(it has to reach technologies outside the precomputed top 12), but that
only fires on-demand, well after the page has already rendered. Net
effect measured end-to-end: full page load (WASM init + parquet fetch +
all sidebar groups + first table page) dropped from ~3.0s to ~1.0–2.0s
across repeated runs.

Not yet explored, worth revisiting if this goes further: trimming
`v_current.parquet` to only the ~25 columns the frontend actually reads
(it ships all 95 from the real schema) would shrink the file and reduce
decode work on every query; the file is also downloaded in full rather
than range-requested locally (`python -m http.server` doesn't support
`Range`) — GH Pages does, so real deployment should already do better
than these localhost numbers on that front specifically.

## Known rough edges to watch for while testing

- First load pays for the full DuckDB-WASM runtime (~a few MB of wasm/js)
  plus the Parquet fetch. Worth checking real-world load time on a cold
  cache, not just localhost.
- The 28MB file only has ~1 observation per domain right now since the
  90-day rotation hasn't completed a full cycle — `v_current` will grow
  over time as more fields fill in per crawl, but the *row count* (one
  per domain) stays flat. It's `observations` (full history) that grows
  unbounded.
- No error handling yet for "DuckDB-WASM unsupported in this browser" —
  worth checking on older/mobile browsers before treating this as
  production-ready.
