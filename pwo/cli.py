import asyncio
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

from .config import Config
from .crawler import run_crawl
from .db import open_db, insert_observation, write_run_summary
from .dotgov import fetch_dotgov_domains
from .models import DomainObservation
from .parquet_writer import write_observations_parquet

console = Console(force_terminal=True)


def _resolve_db_path(output_dir: str) -> str:
    """Return MotherDuck connection string if token is set, else local file path."""
    token = os.getenv("MOTHERDUCK_TOKEN")
    if token:
        return f"md:pwo?motherduck_token={token}"
    return str(Path(output_dir) / "observations.duckdb")


def _load_csv_domains(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            domain = row.get("domain") or row.get("Domain Name") or row.get("domain name") or ""
            domain = domain.strip().lower()
            if not domain:
                continue
            rows.append({
                "domain": domain,
                "source": row.get("source") or "csv",
                "org_name": row.get("org_name") or row.get("Organization") or None,
                "org_type": row.get("org_type") or row.get("Domain Type") or None,
                "state": row.get("state") or row.get("State") or None,
                "ein": row.get("ein") or None,
            })
    return rows


@click.group()
def cli():
    pass


@cli.command()
@click.option("--limit", default=1000, show_default=True, help="Max domains to crawl")
@click.option("--concurrency", default=10, show_default=True)
@click.option("--timeout", default=15, show_default=True, help="Per-request timeout in seconds")
@click.option("--output-dir", default="outputs", show_default=True)
@click.option("--user-agent", default=None, help="Override the User-Agent string")
@click.option("--seed", default=42, show_default=True, help="Random seed for domain sampling")
@click.option("--out-parquet", default=None, help="Write results to this Parquet file instead of the database (no MotherDuck/DuckDB involved)")
def dotgov(limit, concurrency, timeout, output_dir, user_agent, seed, out_parquet):
    """Crawl a random sample of .gov domains from the CISA dotgov dataset."""
    config = Config(
        concurrency=concurrency,
        timeout=timeout,
        output_dir=output_dir,
        db_path=_resolve_db_path(output_dir) if not out_parquet else "",
    )
    if user_agent:
        config.user_agent = user_agent

    console.print("[bold]Fetching dotgov domain list…[/bold]")
    try:
        domain_records = fetch_dotgov_domains(limit=limit, seed=seed)
    except Exception as e:
        console.print(f"[red]Failed to fetch dotgov CSV: {e}[/red]")
        sys.exit(1)

    console.print(f"[green]Sampled {len(domain_records)} domains[/green]")
    _run(domain_records, config, out_parquet=out_parquet)


@cli.command()
@click.argument("csv_path")
@click.option("--limit", default=None, type=int, help="Max domains to crawl")
@click.option("--concurrency", default=10, show_default=True)
@click.option("--timeout", default=15, show_default=True)
@click.option("--output-dir", default="outputs", show_default=True)
@click.option("--user-agent", default=None)
@click.option("--out-parquet", default=None, help="Write results to this Parquet file instead of the database (no MotherDuck/DuckDB involved)")
def crawl(csv_path, limit, concurrency, timeout, output_dir, user_agent, out_parquet):
    """Crawl domains from a CSV file."""
    config = Config(
        concurrency=concurrency,
        timeout=timeout,
        output_dir=output_dir,
        db_path=_resolve_db_path(output_dir) if not out_parquet else "",
    )
    if user_agent:
        config.user_agent = user_agent

    domain_records = _load_csv_domains(csv_path)
    if limit:
        domain_records = domain_records[:limit]
    console.print(f"[green]Loaded {len(domain_records)} domains from {csv_path}[/green]")
    _run(domain_records, config, out_parquet=out_parquet)


@cli.command()
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=None, type=int, help="Port (defaults to $PORT env var, then 8000)")
@click.option("--reload", is_flag=True, default=False)
def serve(host, port, reload):
    """Start the FastAPI server."""
    import uvicorn
    port = port or int(os.getenv("PORT", 8000))
    uvicorn.run("pwo.api:app", host=host, port=port, reload=reload)


@cli.command("download-geoip")
@click.option("--license-key", default=None, envvar="MAXMIND_LICENSE_KEY", help="MaxMind license key")
@click.option("--dest", default="data/GeoLite2-ASN.mmdb", show_default=True, help="Output path")
def download_geoip(license_key, dest):
    """Download the MaxMind GeoLite2-ASN database."""
    import tarfile
    import tempfile
    import urllib.request

    if not license_key:
        console.print("[red]Error:[/red] MAXMIND_LICENSE_KEY not set. Pass --license-key or set the env var.")
        raise SystemExit(1)

    url = (
        f"https://download.maxmind.com/app/geoip_download"
        f"?edition_id=GeoLite2-ASN&license_key={license_key}&suffix=tar.gz"
    )
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    console.print("Downloading GeoLite2-ASN...")
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        urllib.request.urlretrieve(url, tmp.name)
        with tarfile.open(tmp.name, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith(".mmdb"):
                    member.name = dest_path.name
                    tar.extract(member, path=str(dest_path.parent))
                    break

    size_mb = dest_path.stat().st_size / 1_000_000
    console.print(f"[green]Saved[/green] {dest_path} ({size_mb:.1f} MB)")


def _summarize_observations(observations: list[DomainObservation]) -> dict:
    """Same categorization as db.py's write_run_summary(), computed in
    memory instead of via a DB query — used by the --out-parquet path,
    which never opens a database connection."""
    total = len(observations)
    failed_statuses = {"dns_failed", "tls_failed", "timeout", "connection_refused", "unknown_error"}
    ok = sum(1 for o in observations if o.collection_status == "ok")
    failed = sum(1 for o in observations if o.collection_status in failed_statuses)
    blocked = sum(1 for o in observations if o.homepage_block_type != "none")
    with_robots = sum(1 for o in observations if o.robots_txt_available)
    with_sitemap = sum(1 for o in observations if o.sitemap_xml_available)
    with_llms = sum(1 for o in observations if o.llms_txt_available)
    with_tls = sum(1 for o in observations if o.tls_valid)
    response_times = [o.homepage_response_time_ms for o in observations if o.homepage_response_time_ms is not None]
    avg_ms = sum(response_times) / len(response_times) if response_times else None
    return {
        "domains_total": total,
        "domains_ok": ok,
        "domains_failed": failed,
        "domains_blocked": blocked,
        "domains_with_robots_txt": with_robots,
        "domains_with_sitemap_xml": with_sitemap,
        "domains_with_llms_txt": with_llms,
        "domains_with_valid_tls": with_tls,
        "average_response_time_ms": round(avg_ms, 1) if avg_ms else None,
    }


def _run(domain_records: list[dict], config: Config, out_parquet: str | None = None):
    started_at = datetime.now(timezone.utc)
    run_id = started_at.strftime("%Y%m%dT%H%M%SZ")
    total = len(domain_records)

    if out_parquet:
        # No DB, no MotherDuck — collect results in memory and write one
        # Parquet file at the end. Fine for a daily slice (a few thousand
        # rows); not meant for arbitrarily large batches.
        results: list[DomainObservation] = []

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Crawling…", total=total)

            def on_result(obs: DomainObservation):
                results.append(obs)
                progress.advance(task)

            asyncio.run(run_crawl(domain_records, config, run_id, on_result=on_result))

        write_observations_parquet(results, out_parquet)
        finished_at = datetime.now(timezone.utc)
        summary = _summarize_observations(results)

        console.print()
        console.print(f"[bold green]Run complete[/bold green] — {(finished_at - started_at).total_seconds():.1f}s")
        console.print(f"  Total:        {summary['domains_total']}")
        console.print(f"  OK:           {summary['domains_ok']}")
        console.print(f"  Blocked:      {summary['domains_blocked']}")
        console.print(f"  Failed:       {summary['domains_failed']}")
        console.print(f"  robots.txt:   {summary['domains_with_robots_txt']}")
        console.print(f"  sitemap.xml:  {summary['domains_with_sitemap_xml']}")
        console.print(f"  llms.txt:     {summary['domains_with_llms_txt']}")
        console.print(f"  Valid TLS:    {summary['domains_with_valid_tls']}")
        if summary["average_response_time_ms"]:
            console.print(f"  Avg resp:     {summary['average_response_time_ms']:.0f}ms")
        console.print(f"\n  Parquet: {out_parquet}")
        return

    Path(config.output_dir).mkdir(parents=True, exist_ok=True)
    conn = open_db(config.db_path)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Crawling…", total=total)

        def on_result(obs: DomainObservation):
            insert_observation(conn, obs)
            progress.advance(task)

        asyncio.run(run_crawl(domain_records, config, run_id, on_result=on_result))

    finished_at = datetime.now(timezone.utc)
    summary = write_run_summary(conn, run_id, started_at, finished_at, config.crawler_version)
    conn.close()

    console.print()
    console.print(f"[bold green]Run complete[/bold green] — {summary['duration_seconds']:.1f}s")
    console.print(f"  Total:        {summary['domains_total']}")
    console.print(f"  OK:           {summary['domains_ok']}")
    console.print(f"  Blocked:      {summary['domains_blocked']}")
    console.print(f"  Failed:       {summary['domains_failed']}")
    console.print(f"  robots.txt:   {summary['domains_with_robots_txt']}")
    console.print(f"  sitemap.xml:  {summary['domains_with_sitemap_xml']}")
    console.print(f"  llms.txt:     {summary['domains_with_llms_txt']}")
    console.print(f"  Valid TLS:    {summary['domains_with_valid_tls']}")
    if summary["average_response_time_ms"]:
        console.print(f"  Avg resp:     {summary['average_response_time_ms']:.0f}ms")
    console.print(f"\n  DB: {config.db_path}")
