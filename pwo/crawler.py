import asyncio
from datetime import datetime, timezone

import httpx

from .config import Config
from .models import DomainObservation
from . import dns_checks, http_checks, tls_checks, machine_files, html_checks
from . import accessibility_static, hosting_hints, status as status_mod


async def crawl_domain(
    domain_record: dict,
    client: httpx.AsyncClient,
    config: Config,
    run_id: str,
) -> DomainObservation:
    domain = domain_record["domain"].lower().strip()
    data: dict = {
        "domain": domain,
        "source": domain_record.get("source"),
        "org_name": domain_record.get("org_name"),
        "org_type": domain_record.get("org_type"),
        "state": domain_record.get("state"),
        "ein": domain_record.get("ein"),
        "sector": domain_record.get("sector"),
        "run_id": run_id,
        "checked_at": datetime.now(timezone.utc),
        "crawler_version": config.crawler_version,
    }

    try:
        # DNS
        data["stage"] = "dns"
        dns_result = await dns_checks.check_dns(domain)
        data.update(dns_result)

        if not data.get("dns_resolves"):
            data["collection_status"] = "dns_failed"
            data.pop("stage", None)
            return DomainObservation(**data)

        # HTTP + HTTPS (parallel inside check_homepage)
        data["stage"] = "http"
        http_result = await http_checks.check_homepage(client, domain, config)
        html_body: bytes | None = http_result.pop("_html_body", None)
        data.update(http_result)

        # TLS (only if HTTPS responded at all)
        if data.get("https_available"):
            data["stage"] = "tls"
            tls_result = await tls_checks.check_tls(domain, config.timeout)
            data.update(tls_result)

        # Machine-readable files
        data["stage"] = "robots"
        machine_result = await machine_files.check_machine_files(client, domain, config)
        data.update(machine_result)

        # HTML parse + parked detection
        if html_body:
            data["stage"] = "html_parse"
            data.update(html_checks.parse_html(html_body, data.get("homepage_final_url")))
            data.update(html_checks.detect_parked(html_body, data.get("homepage_final_url")))

            # Static accessibility
            data["stage"] = "static_a11y"
            data.update(accessibility_static.check_static_a11y(html_body))

        # Hosting hints (needs primary_ip from DNS)
        if data.get("primary_ip"):
            data["stage"] = "hosting"
            hosting_result = await hosting_hints.check_hosting(
                data["primary_ip"],
                data.get("cname_records", []),
                data.get("ns_records", []),
            )
            data.update(hosting_result)

        data.pop("stage", None)
        data["collection_status"] = status_mod.determine_status(data)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        data["error_type"] = type(e).__name__
        data["error_message"] = str(e)[:500]
        data.setdefault("collection_status", "unknown_error")

    return DomainObservation(**data)


async def run_crawl(
    domain_records: list[dict],
    config: Config,
    run_id: str,
    on_result=None,
) -> list[DomainObservation]:
    semaphore = asyncio.Semaphore(config.concurrency)
    results: list[DomainObservation] = []

    headers = {"User-Agent": config.user_agent}
    limits = httpx.Limits(max_connections=config.concurrency * 2, max_keepalive_connections=config.concurrency)
    timeout = httpx.Timeout(config.timeout, connect=10)

    async with httpx.AsyncClient(headers=headers, limits=limits, timeout=timeout) as client:

        async def bounded(record: dict) -> DomainObservation:
            async with semaphore:
                obs = await crawl_domain(record, client, config, run_id)
                if on_result:
                    on_result(obs)
                return obs

        tasks = [asyncio.create_task(bounded(r)) for r in domain_records]
        results = await asyncio.gather(*tasks, return_exceptions=False)

    return list(results)
