from typing import Optional
from bs4 import BeautifulSoup

PARKED_PHRASES = [
    "domain for sale",
    "buy this domain",
    "parked free",
    "coming soon",
    "under construction",
    "this domain is parked",
    "website expired",
    "account suspended",
    "domain has expired",
    "renewal required",
]

SOCIAL_DOMAINS = {
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "linktr.ee": "linktree",
    "x.com": "x",
    "twitter.com": "x",
}


def parse_html(body: bytes, final_url: Optional[str] = None) -> dict:
    try:
        soup = BeautifulSoup(body, "lxml")
    except Exception:
        soup = BeautifulSoup(body, "html.parser")

    title_tag = soup.find("title")
    title_text = title_tag.get_text(strip=True) if title_tag else None

    meta_desc = soup.find("meta", attrs={"name": lambda v: v and v.lower() == "description"})
    canonical = soup.find("link", attrs={"rel": lambda v: v and "canonical" in (v if isinstance(v, list) else [v])})
    viewport = soup.find("meta", attrs={"name": lambda v: v and v.lower() == "viewport"})

    html_tag = soup.find("html")
    lang = html_tag.get("lang") if html_tag else None

    h1s = soup.find_all("h1")

    return {
        "page_title": title_text,
        "page_title_present": bool(title_text),
        "meta_description_present": meta_desc is not None,
        "html_lang_present": bool(lang),
        "canonical_url": canonical.get("href") if canonical else None,
        "mobile_viewport_present": viewport is not None,
        "h1_count": len(h1s),
    }


def detect_parked(body: bytes, final_url: Optional[str] = None) -> dict:
    result = {
        "is_probably_parked": False,
        "parked_reason": None,
        "social_only_redirect": False,
        "social_only_platform": None,
    }

    # Check if final URL redirected to a social platform
    if final_url:
        for social_domain, platform in SOCIAL_DOMAINS.items():
            if social_domain in final_url:
                result["social_only_redirect"] = True
                result["social_only_platform"] = platform
                result["is_probably_parked"] = True
                result["parked_reason"] = f"redirects_to_{platform}"
                return result

    try:
        text = body.decode("utf-8", errors="replace").lower()
    except Exception:
        return result

    for phrase in PARKED_PHRASES:
        if phrase in text:
            result["is_probably_parked"] = True
            result["parked_reason"] = phrase
            break

    return result
