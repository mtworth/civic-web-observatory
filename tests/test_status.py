from pwo.status import determine_status


def test_dns_failure_short_circuits_everything(sample_observation_dict):
    data = sample_observation_dict(dns_resolves=False, homepage_status_code=200)
    assert determine_status(data) == "dns_failed"


def test_block_type_mapping(sample_observation_dict):
    cases = {
        "timeout": "timeout",
        "rate_limited": "http_429",
        "captcha": "captcha",
        "bot_challenge": "bot_challenge",
        "cloudflare_block": "blocked_by_cdn",
        "forbidden": "http_403",
    }
    for block_type, expected in cases.items():
        data = sample_observation_dict(homepage_block_type=block_type)
        assert determine_status(data) == expected, block_type


def test_block_type_precedes_error_string_matching(sample_observation_dict):
    data = sample_observation_dict(
        homepage_block_type="captcha",
        homepage_error="connect_error: something",
    )
    assert determine_status(data) == "captcha"


def test_error_string_timeout(sample_observation_dict):
    data = sample_observation_dict(homepage_error="request timeout after 10s")
    assert determine_status(data) == "timeout"


def test_error_string_connection_refused_variants(sample_observation_dict):
    data = sample_observation_dict(homepage_error="connect_error: dial tcp")
    assert determine_status(data) == "connection_refused"

    data = sample_observation_dict(homepage_error="Connection Refused by peer")
    assert determine_status(data) == "connection_refused"


def test_error_string_too_many_redirects(sample_observation_dict):
    data = sample_observation_dict(homepage_error="too_many_redirects: exceeded 20")
    assert determine_status(data) == "too_many_redirects"


def test_status_code_fallback_when_no_block_or_error(sample_observation_dict):
    data = sample_observation_dict(homepage_block_type="none", homepage_error=None, homepage_status_code=403)
    assert determine_status(data) == "http_403"

    data = sample_observation_dict(homepage_block_type="none", homepage_error=None, homepage_status_code=429)
    assert determine_status(data) == "http_429"


def test_non_html_homepage(sample_observation_dict):
    data = sample_observation_dict(homepage_status_code=200, homepage_content_type="application/json")
    assert determine_status(data) == "non_html_homepage"


def test_non_html_check_requires_content_type_present(sample_observation_dict):
    data = sample_observation_dict(homepage_status_code=200, homepage_content_type=None)
    assert determine_status(data) == "ok"


def test_parked_overrides_ok_path(sample_observation_dict):
    data = sample_observation_dict(homepage_status_code=200, is_probably_parked=True)
    assert determine_status(data) == "parked_or_placeholder"


def test_connection_refused_when_no_scheme_available(sample_observation_dict):
    data = sample_observation_dict(
        https_available=False,
        http_available=False,
        homepage_status_code=None,
        homepage_content_type=None,
    )
    assert determine_status(data) == "connection_refused"


def test_ok_happy_path(sample_observation_dict):
    data = sample_observation_dict()
    assert determine_status(data) == "ok"


def test_tls_failed_on_cert_verification_error(sample_observation_dict):
    data = sample_observation_dict(tls_error="cert_verification: unable to verify")
    assert determine_status(data) == "tls_failed"


def test_tls_error_without_cert_verification_substring_is_ignored(sample_observation_dict):
    data = sample_observation_dict(tls_error="handshake timeout")
    assert determine_status(data) == "ok"


def test_unknown_error_fallback(sample_observation_dict):
    data = sample_observation_dict(homepage_status_code=500, homepage_content_type=None)
    assert determine_status(data) == "unknown_error"
