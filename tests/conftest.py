from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_observation_dict():
    def _factory(**overrides):
        data = {
            "dns_resolves": True,
            "homepage_block_type": "none",
            "homepage_error": None,
            "homepage_status_code": 200,
            "homepage_content_type": "text/html; charset=utf-8",
            "is_probably_parked": False,
            "https_available": True,
            "http_available": True,
            "tls_error": None,
        }
        data.update(overrides)
        return data

    return _factory


@pytest.fixture
def html_fixture():
    def _load(name: str) -> bytes:
        return (FIXTURES_DIR / "html" / name).read_bytes()

    return _load
