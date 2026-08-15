"""Token-free polling primitives: RSS/Atom parsing, HTML link extraction,
plain-text conversion, fetching, and paywall detection. Stdlib only.
"""
from __future__ import annotations

import gzip
import hashlib
import re
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin

from .watchlist import normalize_url

USER_AGENT = ("StewartCoReaderBot/2.0 (+research scanner; contact "
              "coen@stewartandco.org)")

PAYWALL_STATUSES = {401, 402, 403}
PAYWALL_MARKERS = (
    "subscribe now to continue", "subscribers only", "sign in to read",
    "this content is for members", "purchase this article", "metered paywall",
    "already a subscriber",
)


def item_id(source_id: str, link: str) -> str:
    """Stable 16-hex identity for a feed item, tracking-params stripped."""
    key = f"{source_id}\n{normalize_url(link)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node, *names) -> str | None:
    wanted = {n.lower() for n in names}
    for child in node:
        if _local(child.tag) in wanted and child.text and child.text.strip():
            return unescape(child.text.strip())
    return None


def parse_feed(xml_text: str, source_id: str) -> list[dict]:
    """Parse RSS 2.0 or Atom into item dicts; unparseable feeds return []."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = []
    nodes = [n for n in root.iter() if _local(n.tag) in ("item", "entry")]
    for node in nodes:
        link = _child_text(node, "link")
        if link is None:  # Atom: link is an attribute
            for child in node:
                if _local(child.tag) == "link" and child.get("href"):
                    rel = child.get("rel", "alternate")
                    if rel == "alternate":
                        link = child.get("href")
                        break
        if not link:
            continue
        title = _child_text(node, "title") or "(untitled)"
        summary = _child_text(node, "description", "summary", "content") or ""
        published = _child_text(node, "pubDate", "published", "updated", "dc:date")
        items.append({
            "source_id": source_id,
            "item_id": item_id(source_id, link),
            "title": title,
            "link": link,
            "summary": re.sub(r"<[^>]+>", " ", summary).strip()[:2000],
            "published": published,
        })
    return items


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def extract_links(html_text: str, base_url: str) -> list[tuple[str, str]]:
    """Absolute http(s) links with anchor text, deduped on normalized URL."""
    parser = _LinkParser()
    parser.feed(html_text)
    out, seen = [], set()
    for href, text in parser.links:
        href = (href or "").strip()
        if not href or href.startswith("#"):
            continue
        absolute = urljoin(base_url, href).split("#", 1)[0]
        if not absolute.startswith(("http://", "https://")):
            continue
        key = normalize_url(absolute)
        if key in seen:
            continue
        seen.add(key)
        out.append((absolute, text))
    return out


_FEED_LINK = re.compile(
    r"""<link[^>]+(?:type=["']application/(?:rss|atom)\+xml["'][^>]*"""
    r"""href=["']([^"']+)["']|href=["']([^"']+)["'][^>]*"""
    r"""type=["']application/(?:rss|atom)\+xml["'])""",
    re.IGNORECASE)

# Listing-page links that are navigation/taxonomy, never articles.
NON_ARTICLE_PATTERNS = (
    "/tag/", "/tags/", "/category/", "/categories/", "/author/", "/page/",
    "/feed", "/rss", "/comments", "/wp-login", "/wp-admin", "/search",
    "/about", "/contact", "/privacy", "/terms", "/subscribe", "/login",
    "/signup", "/cart", "/shop", "/product", "?share=", "?replytocom=",
)


def discover_feed(html_text: str, base_url: str) -> str | None:
    """Find a page's declared RSS/Atom feed (<link rel=alternate ...>)."""
    m = _FEED_LINK.search(html_text)
    if not m:
        return None
    href = m.group(1) or m.group(2)
    return urljoin(base_url, href.strip()) if href else None


def article_links(html_text: str, base_url: str,
                  cap: int = 50) -> list[tuple[str, str]]:
    """Same-site links that plausibly point at articles, capped per cycle.

    HTML-diff mode without this treats every nav/sidebar/archive link as an
    item - the 2026-08-15 link-soup incident (600+ 'items' from one blog)."""
    from urllib.parse import urlsplit
    base_host = urlsplit(base_url).netloc.lower().removeprefix("www.")
    out = []
    for url, text in extract_links(html_text, base_url):
        parts = urlsplit(url)
        if parts.netloc.lower().removeprefix("www.") != base_host:
            continue
        path = parts.path.rstrip("/")
        if not path or path.count("/") < 1:       # site root / bare sections
            continue
        lowered = url.lower()
        if any(p in lowered for p in NON_ARTICLE_PATTERNS):
            continue
        if len(path.rsplit("/", 1)[-1]) < 6:      # /nav, /x - not a slug
            continue
        out.append((url, text))
        if len(out) >= cap:
            break
    return out


class _TextParser(HTMLParser):
    SKIP = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__()
        self.chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.chunks.append(data)


def html_to_text(html_text: str) -> str:
    parser = _TextParser()
    parser.feed(html_text)
    return "\n".join(" ".join(c.split()) for c in parser.chunks)


def looks_paywalled(status: int, text: str) -> bool:
    if status in PAYWALL_STATUSES:
        return True
    lowered = text[:20000].lower()
    return any(marker in lowered for marker in PAYWALL_MARKERS)


def fetch_url(url: str, timeout: int = 30) -> tuple[int, str, str]:
    """GET a URL politely. Returns (status, text, final_url); network errors
    surface as status 0 with the error message as text."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.status, raw.decode(charset, errors="replace"), resp.url
    except urllib.error.HTTPError as exc:
        return exc.code, str(exc), url
    except Exception as exc:  # URLError, timeout, decode issues
        return 0, f"{type(exc).__name__}: {exc}", url
