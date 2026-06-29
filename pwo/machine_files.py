import re
from urllib.robotparser import RobotFileParser
from io import StringIO

import httpx

from .config import Config


async def _fetch_file(
    client: httpx.AsyncClient,
    url: str,
    config: Config,
) -> dict:
    result: dict = {
        "status_code": None,
        "available": False,
        "size_bytes": None,
        "error": None,
        "body": None,
    }
    try:
        resp = await client.get(url, follow_redirects=True)
        result["status_code"] = resp.status_code
        if resp.status_code == 200:
            body = resp.content[: config.max_response_bytes]
            result["available"] = True
            result["size_bytes"] = len(body)
            result["body"] = body
    except httpx.TimeoutException:
        result["error"] = "timeout"
    except Exception as e:
        result["error"] = str(e)[:200]
    return result


def _parse_robots(body: bytes, domain: str) -> dict:
    try:
        text = body.decode("utf-8", errors="replace")
        parser = RobotFileParser()
        parser.parse(StringIO(text).readlines())

        allows_homepage = parser.can_fetch(
            "CivicWebIndexBot", f"https://{domain}/"
        )

        sitemaps = re.findall(r"(?i)^Sitemap:\s*(\S+)", text, re.MULTILINE)
        return {
            "robots_allows_homepage": allows_homepage,
            "robots_sitemaps_listed": len(sitemaps),
        }
    except Exception:
        return {"robots_allows_homepage": None, "robots_sitemaps_listed": None}


def _count_sitemap_urls(body: bytes) -> int:
    try:
        text = body.decode("utf-8", errors="replace")
        return len(re.findall(r"<loc>", text, re.IGNORECASE))
    except Exception:
        return 0


def _parse_llms(body: bytes) -> dict:
    try:
        text = body.decode("utf-8", errors="replace")
        has_headings = bool(re.search(r"^#{1,6}\s", text, re.MULTILINE))
        link_count = len(re.findall(r"\[.*?\]\(.*?\)", text))
        return {
            "llms_txt_has_markdown_headings": has_headings,
            "llms_txt_link_count": link_count,
        }
    except Exception:
        return {"llms_txt_has_markdown_headings": None, "llms_txt_link_count": None}


async def check_machine_files(
    client: httpx.AsyncClient,
    domain: str,
    config: Config,
) -> dict:
    import asyncio

    base = f"https://{domain}"
    robots_r, sitemap_r, llms_r = await asyncio.gather(
        _fetch_file(client, f"{base}/robots.txt", config),
        _fetch_file(client, f"{base}/sitemap.xml", config),
        _fetch_file(client, f"{base}/llms.txt", config),
    )

    result: dict = {
        "robots_txt_status_code": robots_r["status_code"],
        "robots_txt_available": robots_r["available"],
        "robots_txt_size_bytes": robots_r["size_bytes"],
        "robots_txt_error": robots_r["error"],
        "robots_allows_homepage": None,
        "robots_sitemaps_listed": None,
        "sitemap_xml_status_code": sitemap_r["status_code"],
        "sitemap_xml_available": sitemap_r["available"],
        "sitemap_xml_size_bytes": sitemap_r["size_bytes"],
        "sitemap_xml_url_count": None,
        "sitemap_xml_error": sitemap_r["error"],
        "llms_txt_status_code": llms_r["status_code"],
        "llms_txt_available": llms_r["available"],
        "llms_txt_size_bytes": llms_r["size_bytes"],
        "llms_txt_has_markdown_headings": None,
        "llms_txt_link_count": None,
        "llms_txt_error": llms_r["error"],
    }

    if robots_r["body"]:
        result.update(_parse_robots(robots_r["body"], domain))

    if sitemap_r["body"]:
        result["sitemap_xml_url_count"] = _count_sitemap_urls(sitemap_r["body"])

    if llms_r["body"]:
        result.update(_parse_llms(llms_r["body"]))

    return result
