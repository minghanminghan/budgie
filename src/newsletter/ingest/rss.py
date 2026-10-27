import calendar
import logging
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx
import lxml.html
import trafilatura

from newsletter.http import PoliteClient, RobotsDisallowed
from newsletter.ingest.base import FIRST_RUN_LIMIT, FetchResult, RawItem, SourceConfig, domain_of
from newsletter.ingest.extract import fetch_and_extract, looks_like_teaser, word_count

log = logging.getLogger(__name__)

FULL_TEXT_MIN_WORDS = 200
SEEN_CAP = 1000
COMMON_PATHS = ("/feed", "/feed/", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/index.xml")
FEED_TYPES = ("application/rss+xml", "application/atom+xml")


def _entry_id(entry) -> str | None:
    return entry.get("id") or entry.get("link")


def _entry_date(entry) -> datetime | None:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(t), UTC) if t else None


def _html_to_text(html: str) -> str:
    if not html.strip():
        return ""
    return trafilatura.html2txt(f"<html><body>{html}</body></html>")


class RssAdapter:
    name = "rss"

    # --- discover -----------------------------------------------------------

    def _try_feed(self, url: str, client: PoliteClient):
        """GET url; return (final_url, parsed, response) or None."""
        try:
            resp = client.get(url)
        except (httpx.HTTPError, RobotsDisallowed):
            return None
        if resp.status_code != 200:
            return None
        return str(resp.url), feedparser.parse(resp.content), resp

    def _config(self, feed_url: str, parsed) -> SourceConfig:
        site = parsed.feed.get("link") or feed_url
        return SourceConfig(
            adapter=self.name,
            config_key=f"rss:{feed_url}",
            config={"feed_url": feed_url},
            title=parsed.feed.get("title") or domain_of(site),
            site_domain=domain_of(site),
            preview_titles=[e.get("title", "") for e in parsed.entries[:3]],
        )

    def _known_patterns(self, url: str) -> list[str]:
        parts = urlsplit(url)
        host = domain_of(url)
        segs = [s for s in parts.path.split("/") if s]
        if host == "substack.com" and segs and segs[0].startswith("@"):
            return [f"https://{segs[0][1:]}.substack.com/feed"]
        if host == "medium.com" and segs and segs[0] != "feed":
            return [f"https://medium.com/feed/{segs[0]}"]
        return []

    def _common_paths(self, url: str) -> list[str]:
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        path = parts.path.rstrip("/")
        out = [root + path + p for p in COMMON_PATHS] if path else []
        out += [root + p for p in COMMON_PATHS]
        return list(dict.fromkeys(out))

    def discover(self, url: str, client: PoliteClient) -> SourceConfig | None:
        tried: set[str] = set()
        candidates: list[str] = []
        first = self._try_feed(url, client)
        tried.add(url)
        if first:
            final_url, parsed, resp = first
            if parsed.entries:
                return self._config(final_url, parsed)
            candidates += self._alternate_links(resp.text, final_url)
        candidates += self._known_patterns(url) + self._common_paths(url)
        for cand in candidates:
            if cand in tried:
                continue
            tried.add(cand)
            got = self._try_feed(cand, client)
            if got and got[1].entries:
                return self._config(got[0], got[1])
        return None

    @staticmethod
    def _alternate_links(html: str, base: str) -> list[str]:
        try:
            doc = lxml.html.fromstring(html)
        except (ValueError, lxml.etree.ParserError):
            return []
        hrefs = []
        for link in doc.xpath('//link[@rel="alternate"][@href]'):
            if (link.get("type") or "").lower() in FEED_TYPES:
                hrefs.append(urljoin(base, link.get("href")))
        return hrefs

    # --- fetch --------------------------------------------------------------

    def fetch(self, config: dict, state: dict, client: PoliteClient) -> FetchResult:
        first_run = not state
        headers = {}
        if state.get("etag"):
            headers["If-None-Match"] = state["etag"]
        if state.get("modified"):
            headers["If-Modified-Since"] = state["modified"]
        resp = client.get(config["feed_url"], headers=headers or None)
        if resp.status_code == 304:
            return FetchResult(items=[], state=dict(state))
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        if not parsed.entries and not parsed.get("version"):
            raise ValueError(f"not a feed: {config['feed_url']}")

        seen = list(state.get("seen", []))
        seen_set = set(seen)
        fresh = [e for e in parsed.entries if _entry_id(e) and _entry_id(e) not in seen_set]
        if first_run:
            fresh.sort(key=lambda e: _entry_date(e) or datetime.min.replace(tzinfo=UTC), reverse=True)
            picked = fresh[:FIRST_RUN_LIMIT]
        else:
            picked = fresh
        items = [self._raw_item(e, client) for e in picked if e.get("link")]

        for e in fresh:
            seen.append(_entry_id(e))
        new_state = {
            "etag": resp.headers.get("ETag"),
            "modified": resp.headers.get("Last-Modified"),
            "seen": list(dict.fromkeys(seen))[-SEEN_CAP:],
        }
        return FetchResult(items=items, state=new_state)

    def _raw_item(self, entry, client: PoliteClient) -> RawItem:
        content = entry.get("content") or []
        html = content[0].get("value", "") if content else entry.get("summary", "")
        text = _html_to_text(html)
        item = RawItem(
            url=entry.link,
            title=entry.get("title", "").strip() or entry.link,
            author=entry.get("author") or None,
            published_at=_entry_date(entry),
            text=text,
            is_teaser=looks_like_teaser(html, text),
        )
        if word_count(text) >= FULL_TEXT_MIN_WORDS:
            return item
        try:
            final_url, ex = fetch_and_extract(entry.link, client)
        except (httpx.HTTPError, RobotsDisallowed) as exc:
            log.info("page fetch failed for %s: %s", entry.link, exc)
            return item
        item.url = final_url
        item.text = ex.text or text
        item.author = item.author or ex.author
        item.published_at = item.published_at or ex.published_at
        item.canonical_link = ex.canonical_link
        item.is_teaser = ex.is_teaser
        return item
