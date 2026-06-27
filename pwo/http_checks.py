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

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Block types that indicate a WAF/CDN gate rather than a network-level failure.
# These are candidates for a browser-UA retry.
_RETRYABLE_BLOCK_TYPES = {
    "cloudflare_block",
    "cloudflare_challenge",
    "akamai_block",
    "imperva_block",
    "sucuri_block",
    "bot_challenge",
    "captcha",
    "forbidden",
    "rate_limited",
}


def _detect_block_type(response: httpx.Response) -> str:
    status = response.status_code
    headers = {k.lower(): v.lower() for k, v in response.headers.items()}
    server = headers.get("server", "")

    if status == 429:
        return "rate_limited"

    if status == 403:
        # Cloudflare-specific hard block
        if "cf-ray" in headers or "cloudflare" in server:
            return "cloudflare_block"
        # Akamai WAF block
        if "akamaighost" in server or "akghost" in server:
            return "akamai_block"
        return "forbidden"

    # Cloudflare hard block via cf-mitigated header
    if "cf-mitigated" in headers:
        return "cloudflare_block"

    # Cloudflare bot challenge (503 or other status with cf-ray)
    if status == 503 and "cf-ray" in headers:
        return "bot_challenge"

    # Imperva / Incapsula — check header first (any status)
    if "x-iinfo" in headers:
        return "imperva_block"

    # Sucuri WAF — check header first (any status)
    if "x-sucuri-id" in headers:
        return "sucuri_block"

    if status in (200, 301, 302, 307, 308):
        try:
            snippet = response.text[:5000].lower()
        except Exception:
            return "none"

        # CAPTCHA pages
        if any(kw in snippet for kw in ["captcha", "are you human", "verify you are human"]):
            return "captcha"

        # Cloudflare JS challenge / browser integrity check
        if "cf-browser-verification" in snippet or "checking your browser" in snippet:
            return "cloudflare_challenge"
        if "challenge-platform" in snippet:
            if "cf-ray" in headers or "cloudflare" in server:
                return "cloudflare_challenge"
            return "bot_challenge"

        # Cloudflare error codes in body (hard access denied)
        if "cf-ray" in headers or "cloudflare" in server:
            for cf_code in ["error 1020", "error 1015", "error 1006", "error 1010"]:
                if cf_code in snippet:
                    return "cloudflare_block"

        # Akamai WAF — server header or body "Reference #" pattern
        if "akamaighost" in server or "akghost" in server:
            return "akamai_block"

        # Imperva / Incapsula — body markers
        if "powered by imperva" in snippet or "incapsula incident" in snippet:
            return "imperva_block"

        # Sucuri — body marker
        if "sucuri website firewall" in snippet:
            return "sucuri_block"

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

    except httpx.TimeoutException:
        result["error"] = "timeout"
        result["block_type"] = "timeout"
    except httpx.TooManyRedirects:
        result["error"] = "too_many_redirects"
    except httpx.ConnectError as e:
        err_str = str(e).lower()
        result["error"] = f"connect_error: {e}"
        if "connection reset" in err_str or "connection refused" in err_str or "errno 54" in err_str or "errno 104" in err_str:
            result["block_type"] = "connection_reset"
        # else block_type stays "none" — it's a network failure, not necessarily a block
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

    # ── Browser UA retry ──
    # When the primary fetch was blocked by a WAF/CDN, attempt one retry with
    # a realistic browser User-Agent. Use a one-shot client to avoid mutating
    # the shared client passed in from run_crawl().
    ua_retry_succeeded = False
    primary_block_type = primary.get("block_type", "none")
    if primary_block_type in _RETRYABLE_BLOCK_TYPES:
        retry_url = https_url if https_ok or not http_ok else http_url
        try:
            async with httpx.AsyncClient(
                headers={"user-agent": BROWSER_UA},
                timeout=client.timeout,
                follow_redirects=True,
            ) as retry_client:
                retry_r = await _fetch(retry_client, retry_url, config)
            if retry_r["success"] and retry_r["block_type"] == "none":
                primary = retry_r
                https_ok = https_ok or retry_url == https_url
                http_ok = http_ok or retry_url == http_url
                ua_retry_succeeded = True
        except Exception:
            pass  # Retry failure is non-fatal; keep original result

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
        "homepage_ua_retry_succeeded": ua_retry_succeeded,
        # transient — used by html_checks, not stored
        "_html_body": primary.get("body"),
    }

