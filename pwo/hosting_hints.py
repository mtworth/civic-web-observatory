import asyncio

PROVIDER_PATTERNS: dict[str, list[str]] = {
    "Cloudflare": ["cloudflare.com", "cloudflare.net", "AS13335"],
    "AWS": ["amazonaws.com", "cloudfront.net", "awsdns", "AS16509", "AS14618"],
    "Azure": ["azure.com", "azurewebsites.net", "trafficmanager.net", "AS8075"],
    "Google": ["googlehosted.com", "google.com", "ghs.google.com", "AS15169", "AS396982"],
    "Fastly": ["fastly.net", "fastlylb.net", "AS54113"],
    "Akamai": ["akamai.net", "akamaiedge.net", "akamaitechnologies.com", "AS16625", "AS20940"],
    "Netlify": ["netlify.com", "netlify.app", "AS394954"],
    "Vercel": ["vercel.app", "vercel-dns.com", "AS76459"],
    "GitHub Pages": ["github.io", "github.com", "github.map.fastly.net"],
    "Pantheon": ["pantheon.io", "pantheonsite.io"],
    "Acquia": ["acquia-sites.com", "acquia.com"],
    "WP Engine": ["wpengine.com", "wpengine.net"],
    "GoDaddy": ["domaincontrol.com", "secureserver.net"],
}


def _match_provider(text: str) -> str:
    text_lower = text.lower()
    for provider, patterns in PROVIDER_PATTERNS.items():
        for pattern in patterns:
            if pattern.lower() in text_lower:
                return provider
    return "unknown"


def _detect_ns_provider(ns_records: list[str]) -> str:
    return _match_provider(" ".join(ns_records)) if ns_records else "unknown"


def _detect_cname_provider(cname_records: list[str]) -> str:
    return _match_provider(" ".join(cname_records)) if cname_records else "unknown"


def _lookup_asn_sync(ip: str) -> dict:
    try:
        from ipwhois import IPWhois
        obj = IPWhois(ip)
        rdap = obj.lookup_rdap(depth=1)
        return {
            "host_asn": rdap.get("asn"),
            "host_asn_org": rdap.get("asn_description"),
            "host_network_name": (rdap.get("network") or {}).get("name"),
        }
    except Exception:
        return {"host_asn": None, "host_asn_org": None, "host_network_name": None}


async def check_hosting(
    primary_ip: str,
    cname_records: list[str],
    ns_records: list[str],
) -> dict:
    loop = asyncio.get_event_loop()
    asn_data = await loop.run_in_executor(None, _lookup_asn_sync, primary_ip)

    asn_hint = ""
    if asn_data.get("host_asn"):
        asn_hint = f"AS{asn_data['host_asn']} {asn_data.get('host_asn_org', '')}"

    ns_hint = _detect_ns_provider(ns_records)
    cname_hint = _detect_cname_provider(cname_records)

    # If ASN matches a provider, let that override "unknown" from DNS
    if ns_hint == "unknown" and asn_hint:
        ns_hint = _match_provider(asn_hint)

    return {
        **asn_data,
        "nameserver_provider_hint": ns_hint,
        "cname_provider_hint": cname_hint,
    }
