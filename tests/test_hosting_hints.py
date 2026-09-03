import pytest

from pwo import hosting_hints


@pytest.mark.asyncio
async def test_check_hosting_returns_empty_result_when_no_reader(monkeypatch):
    monkeypatch.setattr(hosting_hints, "_get_reader", lambda: None)

    result = await hosting_hints.check_hosting("93.184.216.34", [], [])

    assert result == {
        "host_asn": None,
        "host_asn_org": None,
        "nameserver_provider_hint": None,
        "cname_provider_hint": None,
    }


@pytest.mark.asyncio
async def test_check_hosting_returns_empty_result_when_no_primary_ip(monkeypatch):
    class DummyReader:
        def asn(self, ip):
            raise AssertionError("should not be called without a primary_ip")

    monkeypatch.setattr(hosting_hints, "_get_reader", lambda: DummyReader())

    result = await hosting_hints.check_hosting("", [], [])

    assert result["host_asn"] is None


@pytest.mark.asyncio
async def test_check_hosting_extracts_asn_org(monkeypatch):
    class DummyRecord:
        autonomous_system_number = 15169
        autonomous_system_organization = "GOOGLE"

    class DummyReader:
        def asn(self, ip):
            return DummyRecord()

    monkeypatch.setattr(hosting_hints, "_get_reader", lambda: DummyReader())

    result = await hosting_hints.check_hosting("8.8.8.8", [], [])

    assert result["host_asn"] == "15169"
    assert result["host_asn_org"] == "GOOGLE"
    assert result["nameserver_provider_hint"] == "GOOGLE"
    assert result["cname_provider_hint"] is None


@pytest.mark.asyncio
async def test_check_hosting_handles_address_not_found(monkeypatch):
    import geoip2.errors

    class DummyReader:
        def asn(self, ip):
            raise geoip2.errors.AddressNotFoundError("not found")

    monkeypatch.setattr(hosting_hints, "_get_reader", lambda: DummyReader())

    result = await hosting_hints.check_hosting("127.0.0.1", [], [])

    assert result["host_asn"] is None
    assert result["host_asn_org"] is None
