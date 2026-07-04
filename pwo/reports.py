"""Markdown-backed reports/analysis posts.

No CMS, no database table — each post is a markdown file in
reports/posts/ (repo root, alongside data/) with a small frontmatter block:

    ---
    title: Some title
    date: 2026-07-04
    summary: One-line teaser shown on the list page.
    ---
    Markdown body...

Posts are hand-written and committed like any other file in the repo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import markdown as _markdown

POSTS_DIR = Path(__file__).parent.parent / "reports" / "posts"


@dataclass
class Report:
    slug: str
    title: str
    date: str
    summary: str
    body_html: str


def _parse(path: Path) -> Report:
    text = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        _, frontmatter, body = text.split("---", 2)
        for line in frontmatter.strip().splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()
    return Report(
        slug=path.stem,
        title=meta.get("title", path.stem),
        date=meta.get("date", ""),
        summary=meta.get("summary", ""),
        body_html=_markdown.markdown(body.strip(), extensions=["extra"]),
    )


def list_reports() -> list[Report]:
    if not POSTS_DIR.exists():
        return []
    reports = [_parse(p) for p in POSTS_DIR.glob("*.md")]
    return sorted(reports, key=lambda r: r.date, reverse=True)


def get_report(slug: str) -> Report | None:
    path = POSTS_DIR / f"{slug}.md"
    if not path.exists():
        return None
    return _parse(path)
