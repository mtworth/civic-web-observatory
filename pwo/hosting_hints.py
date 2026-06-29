from pathlib import Path

import geoip2.database
import geoip2.errors

_GEOIP_DB_PATH = Path(__file__).parent.parent / "data" / "GeoLite2-ASN.mmdb"

_reader: geoip2.database.Reader | None = None


def _get_reader() -> geoip2.database.Reader | None:
    global _reader
    if _reader is None and _GEOIP_DB_PATH.exists():
        _reader = geoip2.database.Reader(str(_GEOIP_DB_PATH))
    return _reader


async def check_hosting(
    primary_ip: str,
    cname_records: list[str],
    ns_records: list[str],
) -> dict:
    reader = _get_reader()
    if reader is None or not primary_ip:
        return {"host_asn": None, "host_asn_org": None, "nameserver_provider_hint": None, "cname_provider_hint": None}
    try:
        record = reader.asn(primary_ip)
        org = record.autonomous_system_organization
        return {
            "host_asn": str(record.autonomous_system_number),
            "host_asn_org": org,
            "nameserver_provider_hint": org,
            "cname_provider_hint": None,
        }
    except (geoip2.errors.AddressNotFoundError, Exception):
        return {"host_asn": None, "host_asn_org": None, "nameserver_provider_hint": None, "cname_provider_hint": None}
