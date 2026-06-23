import asyncio
import dns.asyncresolver
import dns.exception


async def check_dns(domain: str) -> dict:
    result: dict = {
        "dns_resolves": False,
        "dns_error": None,
        "a_records": [],
        "aaaa_records": [],
        "cname_records": [],
        "ns_records": [],
        "primary_ip": None,
    }

    resolver = dns.asyncresolver.Resolver()
    resolver.timeout = 10
    resolver.lifetime = 10

    async def query(qtype: str) -> list[str]:
        try:
            answers = await resolver.resolve(domain, qtype)
            return [r.to_text().rstrip(".") for r in answers]
        except (dns.exception.DNSException, Exception):
            return []

    a, aaaa, cname, ns = await asyncio.gather(
        query("A"),
        query("AAAA"),
        query("CNAME"),
        query("NS"),
    )

    result["a_records"] = a
    result["aaaa_records"] = aaaa
    result["cname_records"] = cname
    result["ns_records"] = ns

    if a or aaaa:
        result["dns_resolves"] = True
        result["primary_ip"] = a[0] if a else aaaa[0]
    else:
        try:
            # One explicit attempt to surface the error message
            await resolver.resolve(domain, "A")
        except dns.resolver.NXDOMAIN:
            result["dns_error"] = "NXDOMAIN"
        except dns.resolver.NoAnswer:
            result["dns_error"] = "no_answer"
        except dns.resolver.NoNameservers:
            result["dns_error"] = "no_nameservers"
        except dns.exception.Timeout:
            result["dns_error"] = "timeout"
        except Exception as e:
            result["dns_error"] = str(e)[:200]

    return result
