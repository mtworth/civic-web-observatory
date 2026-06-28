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
@click.option("--skip-axe", is_flag=True, default=True, show_default=True)
@click.option("--output-dir", default="outputs", show_default=True)
@click.option("--user-agent", default=None, help="Override the User-Agent string")
@click.option("--seed", default=42, show_default=True, help="Random seed for domain sampling")
def dotgov(limit, concurrency, timeout, skip_axe, output_dir, user_agent, seed):
    """Crawl a random sample of .gov domains from the CISA dotgov dataset."""
    config = Config(
        concurrency=concurrency,
        timeout=timeout,
        skip_axe=skip_axe,
        output_dir=output_dir,
        db_path=_resolve_db_path(output_dir),
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
    _run(domain_records, config)


@cli.command()
@click.argument("csv_path")
@click.option("--limit", default=None, type=int, help="Max domains to crawl")
@click.option("--concurrency", default=10, show_default=True)
@click.option("--timeout", default=15, show_default=True)
@click.option("--skip-axe", is_flag=True, default=True, show_default=True)
@click.option("--output-dir", default="outputs", show_default=True)
@click.option("--user-agent", default=None)
def crawl(csv_path, limit, concurrency, timeout, skip_axe, output_dir, user_agent):
    """Crawl domains from a CSV file."""
    config = Config(
        concurrency=concurrency,
        timeout=timeout,
        skip_axe=skip_axe,
        output_dir=output_dir,
        db_path=_resolve_db_path(output_dir),
    )
    if user_agent:
        config.user_agent = user_agent

    domain_records = _load_csv_domains(csv_path)
    if limit:
        domain_records = domain_records[:limit]
    console.print(f"[green]Loaded {len(domain_records)} domains from {csv_path}[/green]")
    _run(domain_records, config)


@cli.command()
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8000, show_default=True)
@click.option("--reload", is_flag=True, default=False)
def serve(host, port, reload):
    """Start the FastAPI server."""
    import uvicorn
    uvicorn.run("pwo.api:app", host=host, port=port, reload=reload)


def _run(domain_records: list[dict], config: Config):
    Path(config.output_dir).mkdir(parents=True, exist_ok=True)
    conn = open_db(config.db_path)

    started_at = datetime.now(timezone.utc)
    run_id = started_at.strftime("%Y%m%dT%H%M%SZ")
    total = len(domain_records)

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
