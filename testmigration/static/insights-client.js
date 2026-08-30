// Loads Markdown-backed Insights posts client-side — mirrors the exact
// frontmatter format and parsing logic of pwo/reports.py's _parse(), just
// running in the browser against static .md files instead of a server glob.
//
// A manifest.json lists available post slugs since a static host has no
// directory listing to glob against; add a new post's slug there when you
// add a new insights/posts/<slug>.md file.

const POSTS_DIR = "insights/posts";
const MANIFEST_URL = "insights/manifest.json";

export function parseFrontmatter(text) {
  const meta = {};
  let body = text;
  if (text.startsWith("---")) {
    // mirrors Python's text.split("---", 2): only the first two delimiters
    // count, so a markdown horizontal rule ("---") later in the body is
    // left alone rather than truncating the post.
    const closingIdx = text.indexOf("---", 3);
    if (closingIdx !== -1) {
      const frontmatter = text.slice(3, closingIdx);
      body = text.slice(closingIdx + 3);
      for (const line of frontmatter.trim().split("\n")) {
        const idx = line.indexOf(":");
        if (idx === -1) continue;
        const key = line.slice(0, idx).trim();
        const value = line.slice(idx + 1).trim();
        meta[key] = value;
      }
    }
  }
  return { meta, body: body.trim() };
}

export async function fetchManifest() {
  const res = await fetch(MANIFEST_URL);
  if (!res.ok) return [];
  return res.json();
}

export async function fetchPost(slug) {
  const res = await fetch(`${POSTS_DIR}/${slug}.md`);
  if (!res.ok) return null;
  const text = await res.text();
  const { meta, body } = parseFrontmatter(text);
  return {
    slug,
    title: meta.title || slug,
    date: meta.date || "",
    summary: meta.summary || "",
    bodyMarkdown: body,
  };
}

export async function fetchAllPosts() {
  const slugs = await fetchManifest();
  const posts = await Promise.all(slugs.map(fetchPost));
  return posts
    .filter(Boolean)
    .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
}
