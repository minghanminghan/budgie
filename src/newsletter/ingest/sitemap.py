import gzip
import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx

from newsletter.http import PoliteClient, RobotsDisallowed
from newsletter.ingest.base import FIRST_RUN_LIMIT, FetchResult, RawItem, SourceConfig, domain_of
from newsletter.ingest.extract import fetch_and_extract, parse_datetime

log = logging.getLogger(__name__)

MAX_DEPTH = 3
SEEN_CAP = 20000
MAX_DISCOVER_FETCHES = 40
MAX_FETCH_PER_RUN = 20
STALE_AFTER = timedelta(days=7)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(el: ET.Element, name: str) -> str | None:
    for child in el:
        if _local(child.tag) == name and child.text:
            return child.text.strip()
    return None


def parse_sitemap(data: bytes) -> tuple[list[str], list[tuple[str, datetime | None]]]:
    """Return (child sitemap URLs, page URLs with lastmod). Gzip is detected by magic bytes."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    root = ET.fromstring(data)
    children: list[str] = []
    pages: list[tuple[str, datetime | None]] = []
    kind = _local(root.tag)
    for el in root:
        loc = _child_text(el, "loc")
        if not loc:
            continue
        if kind == "sitemapindex" and _local(el.tag) == "sitemap":
            children.append(loc)
        elif kind == "urlset" and _local(el.tag) == "url":
            pages.append((loc, parse_datetime(_child_text(el, "lastmod"))))
    return children, pages


def _robots_sitemaps(site: str, client: PoliteClient) -> list[str]:
    try:
        resp = client.get(f"{site}/robots.txt")
    except (httpx.HTTPError, RobotsDisallowed):
        return []
    if resp.status_code != 200:
        return []
    return re.findall(r"(?im)^\s*sitemap:\s*(\S+)", resp.text)


def _slug_title(url: str) -> str:
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return slug.replace("-", " ").replace("_", " ").strip() or url


class SitemapAdapter:
    name = "sitemap"

    def _collect(self, sitemaps: list[str], host: str, prefix: str, client: PoliteClient,
                 strict: bool, budget: int | None = None) -> tuple[dict[str, datetime | None], list[str]]:
        """Page URLs on `host` strictly under `prefix` (mapped to lastmod), and the
        leaf sitemaps that held them. `budget` caps sitemap requests; children that
        look related to `prefix` are tried first, and once matches are found the
        unrelated children are skipped."""
        found: dict[str, datetime | None] = {}
        leaves: list[str] = []
        visited: set[str] = set()
        stack = [(sm, 1, True) for sm in reversed(sitemaps)]
        while stack:
            url, depth, is_root = stack.pop()
            if url in visited or depth > MAX_DEPTH or (budget is not None and len(visited) >= budget):
                continue
            visited.add(url)
            try:
                resp = client.get(url)
                resp.raise_for_status()
                children, pages = parse_sitemap(resp.content)
            except (httpx.HTTPError, RobotsDisallowed, ET.ParseError, OSError) as exc:
                if strict and is_root:
                    raise
                log.info("sitemap %s skipped: %s", url, exc)
                continue
            matched = False
            for loc, lastmod in pages:
                parts = urlsplit(loc)
                if domain_of(loc) == host and parts.path.startswith(prefix) and parts.path != prefix:
                    found.setdefault(loc, lastmod)
                    matched = True
            if matched:
                leaves.append(url)
            ranked = sorted(children, key=lambda c: _relevance(c, prefix), reverse=True)
            if budget is not None and found:
                ranked = [c for c in ranked if _relevance(c, prefix) > 0]
            stack.extend((c, depth + 1, False) for c in reversed(ranked))
        return found, leaves

    def discover(self, url: str, client: PoliteClient) -> SourceConfig | None:
        parts = urlsplit(url)
        path = parts.path.rstrip("/")
        if not parts.scheme or not parts.netloc or not path:
            return None
        site = f"{parts.scheme}://{parts.netloc}"
        prefix = path + "/"
        sitemaps = _robots_sitemaps(site, client) or [f"{site}/sitemap.xml"]
        host = domain_of(url)
        pages, leaves = self._collect(sitemaps, host, prefix, client, strict=False,
                                      budget=MAX_DISCOVER_FETCHES)
        if not pages:
            return None
        newest = sorted(pages, key=lambda u: _sort_key(pages[u]), reverse=True)[:3]
        return SourceConfig(
            adapter=self.name,
            config_key=f"sitemap:{site}{prefix}",
            config={"site": site, "sitemaps": leaves, "prefix": prefix},
            title=f"{host}{path}",
            site_domain=host,
            preview_titles=[_slug_title(u) for u in newest],
        )

    def fetch(self, config: dict, state: dict, client: PoliteClient) -> FetchResult:
        first_run = not state
        host = domain_of(config["site"])
        pages, _ = self._collect(config["sitemaps"], host, config["prefix"], client, strict=True)
        seen = list(state.get("seen", []))
        seen_set = set(seen)
        fresh = sorted((u for u in pages if u not in seen_set),
                       key=lambda u: _sort_key(pages[u]), reverse=True)
        if first_run:
            to_fetch = fresh[:FIRST_RUN_LIMIT]
            seen += fresh[FIRST_RUN_LIMIT:]
        else:
            # Old URLs that resurface (e.g. a child sitemap that failed before) are
            # marked seen unfetched; the rest drain at most MAX_FETCH_PER_RUN per run.
            cutoff = datetime.now(UTC) - STALE_AFTER
            stale = [u for u in fresh if pages[u] is not None and pages[u] < cutoff]
            seen += stale
            stale_set = set(stale)
            to_fetch = [u for u in fresh if u not in stale_set][:MAX_FETCH_PER_RUN]

        items: list[RawItem] = []
        for url in to_fetch:
            try:
                final_url, ex = fetch_and_extract(url, client)
            except (httpx.HTTPError, RobotsDisallowed) as exc:
                log.info("page %s failed, will retry: %s", url, exc)
                continue
            items.append(RawItem(
                url=final_url,
                title=(ex.title or "").strip() or _slug_title(url),
                author=ex.author,
                published_at=ex.published_at or pages[url],
                text=ex.text,
                canonical_link=ex.canonical_link,
                is_teaser=ex.is_teaser,
            ))
            seen.append(url)
        return FetchResult(items=items, state={"seen": list(dict.fromkeys(seen))[-SEEN_CAP:]})


def _relevance(sitemap_url: str, prefix: str) -> int:
    """How much a child sitemap's name looks like the prefix; later segments weigh more."""
    tokens = set(re.split(r"[^a-z0-9]+", urlsplit(sitemap_url).path.lower()))
    return sum(i + 1 for i, seg in enumerate(prefix.strip("/").lower().split("/")) if seg in tokens)


def _sort_key(lastmod: datetime | None) -> tuple[int, datetime]:
    # missing lastmod sorts last under reverse=True
    return (0, datetime.min.replace(tzinfo=UTC)) if lastmod is None else (1, lastmod)
