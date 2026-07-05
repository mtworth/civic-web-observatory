from pwo.accessibility_static import check_static_a11y


def test_missing_alt_vs_empty_alt_distinction(html_fixture):
    body = html_fixture("a11y_mixed.html")
    result = check_static_a11y(body)

    assert result["static_a11y_ran"] is True
    assert result["a11y_images_total"] == 2
    assert result["a11y_images_missing_alt"] == 1


def test_input_labeling_and_exclusions(html_fixture):
    body = html_fixture("a11y_mixed.html")
    result = check_static_a11y(body)

    # hidden and submit inputs are excluded from the total
    assert result["a11y_inputs_total"] == 3
    # only the truly-unlabeled text input should be flagged
    assert result["a11y_inputs_missing_labels"] == 1


def test_landmarks_detected_via_semantic_tags(html_fixture):
    body = html_fixture("a11y_mixed.html")
    result = check_static_a11y(body)

    assert result["a11y_has_main_landmark"] is True
    assert result["a11y_has_nav_landmark"] is True


def test_landmarks_detected_via_aria_roles(html_fixture):
    body = html_fixture("a11y_role_landmarks.html")
    result = check_static_a11y(body)

    assert result["a11y_has_main_landmark"] is True
    assert result["a11y_has_nav_landmark"] is True


def test_no_landmarks_when_absent(html_fixture):
    body = html_fixture("minimal_empty.html")
    result = check_static_a11y(body)

    assert result["a11y_has_main_landmark"] is False
    assert result["a11y_has_nav_landmark"] is False


def test_malformed_body_returns_safe_shell_without_raising():
    result = check_static_a11y(None)

    assert result["static_a11y_ran"] is False
    assert result["static_a11y_error"] is not None
    assert result["a11y_images_total"] is None
