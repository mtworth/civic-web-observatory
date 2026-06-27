"""
Technology fingerprinting module for the Public Web Observatory.

Implements a subset of the Wappalyzer/enthec detection approach:
- html: regex match against full HTML body text
- headers: regex match against HTTP response header values
- meta: parse <meta name="X" content="Y"> and match Y
- scriptSrc: parse <script src="..."> and match

DOM and JS patterns are skipped (require a browser).

All patterns are pre-compiled at module import time for performance.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Pattern loading and compilation
# ---------------------------------------------------------------------------

_FINGERPRINTS_DIR = Path(__file__).parent / "fingerprints"

# Strip Wappalyzer version-extraction suffix: ";version:\1" etc.
_VERSION_SUFFIX_RE = re.compile(r"\\;version:.*$")


def _clean_pattern(raw: str) -> str:
    """Remove Wappalyzer version-extraction suffix from a raw pattern string."""
    return _VERSION_SUFFIX_RE.sub("", raw)


def _compile(raw: str) -> re.Pattern | None:
    """Compile a pattern string, returning None on error."""
    cleaned = _clean_pattern(raw)
    if not cleaned:
        return None
    try:
        return re.compile(cleaned, re.IGNORECASE)
    except re.error:
        return None


def _load_tech_patterns() -> dict[str, dict]:
    """
    Load all technology JSON files and compile their patterns.

    Returns a dict keyed by technology name, each value being:
    {
        "html": [compiled_re, ...],
        "headers": {lower_header_name: compiled_re, ...},
        "meta": {lower_meta_name: compiled_re, ...},
        "script_src": [compiled_re, ...],
        "implies": [tech_name, ...],
    }
    """
    files = sorted(_FINGERPRINTS_DIR.glob("*.json"))
    # Exclude categories.json
    tech_files = [f for f in files if f.name != "categories.json"]

    all_techs: dict[str, dict] = {}

    for path in tech_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        for tech_name, tech_data in data.items():
            if not isinstance(tech_data, dict):
                continue

            entry: dict = {
                "html": [],
                "headers": {},
                "meta": {},
                "script_src": [],
                "implies": [],
            }

            # html patterns — may be a string or list of strings
            html_patterns = tech_data.get("html", [])
            if isinstance(html_patterns, str):
                html_patterns = [html_patterns]
            for raw in html_patterns:
                if isinstance(raw, str):
                    pat = _compile(raw)
                    if pat is not None:
                        entry["html"].append(pat)

            # headers patterns — dict of {header_name: pattern_string}
            headers = tech_data.get("headers", {})
            if isinstance(headers, dict):
                for hname, hpat in headers.items():
                    if isinstance(hpat, str):
                        pat = _compile(hpat)
                        if pat is not None:
                            entry["headers"][hname.lower()] = pat

            # meta patterns — dict of {meta_name: pattern_string}
            meta = tech_data.get("meta", {})
            if isinstance(meta, dict):
                for mname, mpat in meta.items():
                    if isinstance(mpat, str):
                        pat = _compile(mpat)
                        if pat is not None:
                            entry["meta"][mname.lower()] = pat

            # scriptSrc patterns — may be a string or list of strings
            script_patterns = tech_data.get("scriptSrc", [])
            if isinstance(script_patterns, str):
                script_patterns = [script_patterns]
            for raw in script_patterns:
                if isinstance(raw, str):
                    pat = _compile(raw)
                    if pat is not None:
                        entry["script_src"].append(pat)

            # implies — may be a string, list, or dict
            implies_raw = tech_data.get("implies", [])
            if isinstance(implies_raw, str):
                implies_raw = [implies_raw]
            if isinstance(implies_raw, list):
                for imp in implies_raw:
                    if isinstance(imp, str):
                        # Strip version suffix from implies names too
                        name = _VERSION_SUFFIX_RE.sub("", imp).strip()
                        if name:
                            entry["implies"].append(name)

            all_techs[tech_name] = entry

    return all_techs


# Pre-compile all patterns at module load time
TECH_PATTERNS: dict[str, dict] = _load_tech_patterns()

# Pre-compile meta and script extraction patterns
_META_RE = re.compile(
    r'<meta[^>]+name=["\']([^"\']+)["\'][^>]+content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)
_META_RE2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+name=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Detection logic
# ---------------------------------------------------------------------------

def _extract_metas(html: str) -> dict[str, str]:
    """Extract {lower_name: content} from <meta name="..." content="..."> tags."""
    metas: dict[str, str] = {}
    for m in _META_RE.finditer(html):
        metas[m.group(1).lower()] = m.group(2)
    for m in _META_RE2.finditer(html):
        metas[m.group(2).lower()] = m.group(1)
    return metas


def _extract_script_srcs(html: str) -> list[str]:
    """Extract all script src attribute values."""
    return _SCRIPT_SRC_RE.findall(html)


def detect(html: str, response_headers: dict[str, str]) -> list[str]:
    """
    Detect technologies present in the given HTML and HTTP response headers.

    Parameters
    ----------
    html:
        Full HTML body text of the page.
    response_headers:
        HTTP response headers as a plain dict (header names may be any case).

    Returns
    -------
    Sorted list of detected technology names.
    """
    if not html and not response_headers:
        return []

    # Normalise headers to lowercase keys
    lower_headers: dict[str, str] = {k.lower(): v for k, v in response_headers.items()}

    # Extract reusable artefacts from HTML once
    metas = _extract_metas(html) if html else {}
    script_srcs = _extract_script_srcs(html) if html else []

    detected: set[str] = set()

    for tech_name, entry in TECH_PATTERNS.items():
        matched = False

        # html patterns
        if html and not matched:
            for pat in entry["html"]:
                if pat.search(html):
                    matched = True
                    break

        # header patterns
        if not matched:
            for hname, pat in entry["headers"].items():
                hval = lower_headers.get(hname, "")
                if hval and pat.search(hval):
                    matched = True
                    break

        # meta patterns
        if not matched:
            for mname, pat in entry["meta"].items():
                mval = metas.get(mname, "")
                if mval and pat.search(mval):
                    matched = True
                    break

        # scriptSrc patterns
        if not matched and script_srcs:
            for pat in entry["script_src"]:
                for src in script_srcs:
                    if pat.search(src):
                        matched = True
                        break
                if matched:
                    break

        if matched:
            detected.add(tech_name)

    # Expand implied technologies (one level; most implies chains are shallow)
    implied: set[str] = set()
    for tech_name in detected:
        for imp in TECH_PATTERNS.get(tech_name, {}).get("implies", []):
            if imp in TECH_PATTERNS:
                implied.add(imp)
    detected |= implied

    return sorted(detected)
