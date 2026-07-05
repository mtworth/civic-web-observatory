from pwo.html_checks import parse_html, detect_parked


def test_parse_html_extracts_populated_fields(html_fixture):
    body = html_fixture("basic_ok.html")
    result = parse_html(body)

    assert result["page_title"] == "Example Org Homepage"
    assert result["page_title_present"] is True
    assert result["meta_description_present"] is True
    assert result["html_lang_present"] is True
    assert result["canonical_url"] == "https://example.org/"
    assert result["mobile_viewport_present"] is True
    assert result["h1_count"] == 1


def test_parse_html_degrades_gracefully_when_empty(html_fixture):
    body = html_fixture("minimal_empty.html")
    result = parse_html(body)

    assert result["page_title"] is None
    assert result["page_title_present"] is False
    assert result["meta_description_present"] is False
    assert result["html_lang_present"] is False
    assert result["canonical_url"] is None
    assert result["mobile_viewport_present"] is False
    assert result["h1_count"] == 0


def test_detect_parked_social_redirect_short_circuits_phrase_matching():
    body = b"<html><body>domain for sale, buy this domain now</body></html>"
    result = detect_parked(body, final_url="https://www.facebook.com/somepage")

    assert result["is_probably_parked"] is True
    assert result["social_only_redirect"] is True
    assert result["social_only_platform"] == "facebook"
    assert result["parked_reason"] == "redirects_to_facebook"


def test_detect_parked_phrase_match():
    body = b"<html><body>This domain is parked and available.</body></html>"
    result = detect_parked(body, final_url="https://example.org/")

    assert result["is_probably_parked"] is True
    assert result["social_only_redirect"] is False
    assert result["parked_reason"] == "this domain is parked"


def test_detect_parked_no_match():
    body = b"<html><body>Welcome to our real organization.</body></html>"
    result = detect_parked(body, final_url="https://example.org/")

    assert result["is_probably_parked"] is False
    assert result["parked_reason"] is None


def test_detect_parked_returns_default_when_body_cannot_be_decoded():
    result = detect_parked(None, final_url="https://example.org/")

    assert result == {
        "is_probably_parked": False,
        "parked_reason": None,
        "social_only_redirect": False,
        "social_only_platform": None,
    }
