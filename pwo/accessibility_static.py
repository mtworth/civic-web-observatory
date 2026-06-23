from bs4 import BeautifulSoup


def check_static_a11y(body: bytes) -> dict:
    result = {
        "static_a11y_ran": False,
        "static_a11y_error": None,
        "a11y_has_title": None,
        "a11y_has_html_lang": None,
        "a11y_h1_count": None,
        "a11y_images_total": None,
        "a11y_images_missing_alt": None,
        "a11y_inputs_total": None,
        "a11y_inputs_missing_labels": None,
        "a11y_has_main_landmark": None,
        "a11y_has_nav_landmark": None,
    }
    try:
        soup = BeautifulSoup(body, "lxml")

        html_tag = soup.find("html")
        lang = html_tag.get("lang") if html_tag else None
        title_tag = soup.find("title")
        h1s = soup.find_all("h1")

        images = soup.find_all("img")
        missing_alt = [
            img for img in images
            if not img.get("alt") and img.get("alt") != ""
        ]

        inputs = soup.find_all("input", type=lambda t: t not in ("hidden", "submit", "button", "reset", None) or t is None)
        inputs = [i for i in inputs if i.get("type", "text") not in ("hidden", "submit", "button", "reset")]

        labeled_ids = set()
        for label in soup.find_all("label"):
            if label.get("for"):
                labeled_ids.add(label["for"])

        missing_labels = []
        for inp in inputs:
            inp_id = inp.get("id", "")
            has_aria_label = bool(inp.get("aria-label") or inp.get("aria-labelledby"))
            has_label_for = inp_id and inp_id in labeled_ids
            if not has_aria_label and not has_label_for:
                missing_labels.append(inp)

        has_main = bool(
            soup.find("main") or
            soup.find(attrs={"role": "main"})
        )
        has_nav = bool(
            soup.find("nav") or
            soup.find(attrs={"role": "navigation"})
        )

        result.update({
            "static_a11y_ran": True,
            "a11y_has_title": title_tag is not None and bool(title_tag.get_text(strip=True)),
            "a11y_has_html_lang": bool(lang),
            "a11y_h1_count": len(h1s),
            "a11y_images_total": len(images),
            "a11y_images_missing_alt": len(missing_alt),
            "a11y_inputs_total": len(inputs),
            "a11y_inputs_missing_labels": len(missing_labels),
            "a11y_has_main_landmark": has_main,
            "a11y_has_nav_landmark": has_nav,
        })

    except Exception as e:
        result["static_a11y_error"] = str(e)[:200]

    return result
