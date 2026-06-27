import asyncio
import time
from typing import Optional
import httpx

from .config import Config

BLOCK_KEYWORDS_BODY = [
    "access denied",
    "blocked",
    "forbidden",
    "your ip",
    "captcha",
    "are you human",
    "verify you are human",
    "ddos protection",
    "bot detection",
    "security check",
]

PARKED_KEYWORDS = [
    "domain for sale",
    "buy this domain",
    "parked free",
    "coming soon",
    "under construction",
    "this domain is parked",
    "website expired",
    "account suspended",
]

SOCIAL_DOMAINS = {
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "linktr.ee": "linktree",
    "x.com": "x",
    "twitter.com": "x",
}


def _detect_block_type(response: httpx.Response) -> str:
    status = response.status_code
    headers = {k.lower(): v.lower() for k, v in response.headers.items()}

    if status == 429:
        return "rate_limited"
    if status == 403:
        # Cloudflare-specific 403
        if "cf-ray" in headers or "cloudflare" in headers.get("server", ""):
            return "cloudflare_block"
        return "forbidden"
    if "cf-mitigated" in headers:
        return "cloudflare_block"
    if status == 503 and "cf-ray" in headers:
        return "bot_challenge"

    if status in (200, 301, 302, 307, 308):
        try:
            snippet = response.text[:5000].lower()
        except Exception:
            return "none"
        if any(kw in snippet for kw in ["captcha", "are you human", "verify you are human"]):
            return "captcha"
        if "cf-browser-verification" in snippet or "checking your browser" in snippet:
            return "bot_challenge"
        if "challenge-platform" in snippet:
            return "bot_challenge"

    return "none"


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    config: Config,
) -> dict:
    result: dict = {
        "url": url,
        "final_url": None,
        "status_code": None,
        "content_type": None,
        "response_time_ms": None,
        "size_bytes": None,
        "redirect_count": None,
        "error": None,
        "block_type": "none",
        "body": None,
        "success": False,
        "response_headers": {},
    }
    try:
        start = time.monotonic()
        resp = await client.get(url, follow_redirects=True)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        body = resp.content[: config.max_response_bytes]

        result["final_url"] = str(resp.url)
        result["status_code"] = resp.status_code
        result["content_type"] = resp.headers.get("content-type", "")
        result["response_time_ms"] = elapsed_ms
        result["size_bytes"] = len(body)
        result["redirect_count"] = len(resp.history)
        result["block_type"] = _detect_block_type(resp)
        result["success"] = resp.status_code < 500
        result["body"] = body if resp.status_code == 200 else None
        result["response_headers"] = dict(resp.headers)

    except httpx.TimeoutException:
        result["error"] = "timeout"
        result["block_type"] = "timeout"
    except httpx.TooManyRedirects:
        result["error"] = "too_many_redirects"
    except httpx.ConnectError as e:
        result["error"] = f"connect_error: {e}"
    except Exception as e:
        result["error"] = str(e)[:200]

    return result


async def check_homepage(
    client: httpx.AsyncClient,
    domain: str,
    config: Config,
) -> dict:
    https_url = f"https://{domain}/"
    http_url = f"http://{domain}/"

    https_r, http_r = await asyncio.gather(
        _fetch(client, https_url, config),
        _fetch(client, http_url, config),
    )

    https_ok = https_r["success"] and https_r["status_code"] is not None
    http_ok = http_r["success"] and http_r["status_code"] is not None

    # Prefer HTTPS as the primary homepage source
    primary = https_r if https_ok else http_r

    http_redirects_to_https: Optional[bool] = None
    if http_ok and http_r["final_url"]:
        http_redirects_to_https = http_r["final_url"].startswith("https://")

    return {
        "homepage_attempted": True,
        "homepage_url": https_url,
        "homepage_final_url": primary.get("final_url"),
        "homepage_status_code": primary.get("status_code"),
        "homepage_content_type": primary.get("content_type"),
        "homepage_response_time_ms": primary.get("response_time_ms"),
        "homepage_size_bytes": primary.get("size_bytes"),
        "homepage_redirect_count": primary.get("redirect_count"),
        "homepage_error": primary.get("error"),
        "homepage_block_type": primary.get("block_type", "none"),
        "https_available": https_ok,
        "http_available": http_ok,
        "http_redirects_to_https": http_redirects_to_https,
        # transient — used by html_checks and detector, not stored
        "_html_body": primary.get("body"),
        "_response_headers": primary.get("response_headers", {}),
    }

