def determine_status(data: dict) -> str:
    if not data.get("dns_resolves"):
        return "dns_failed"

    block = data.get("homepage_block_type", "none")
    if block == "timeout":
        return "timeout"
    if block == "rate_limited":
        return "http_429"
    if block == "captcha":
        return "captcha"
    if block in ("bot_challenge",):
        return "bot_challenge"
    if block == "cloudflare_block":
        return "blocked_by_cdn"
    if block == "forbidden":
        return "http_403"

    err = data.get("homepage_error", "")
    if err:
        if "timeout" in (err or ""):
            return "timeout"
        if "connect_error" in (err or "") or "connection refused" in (err or "").lower():
            return "connection_refused"
        if "too_many_redirects" in (err or ""):
            return "too_many_redirects"

    status_code = data.get("homepage_status_code")
    if status_code == 403:
        return "http_403"
    if status_code == 429:
        return "http_429"

    content_type = data.get("homepage_content_type") or ""
    if status_code == 200 and content_type and "text/html" not in content_type.lower():
        return "non_html_homepage"

    if data.get("is_probably_parked"):
        return "parked_or_placeholder"

    if not data.get("https_available") and not data.get("http_available"):
        return "connection_refused"

    if status_code and 200 <= status_code < 400:
        tls_error = data.get("tls_error")
        if tls_error and "cert_verification" in tls_error:
            return "tls_failed"
        return "ok"

    return "unknown_error"
