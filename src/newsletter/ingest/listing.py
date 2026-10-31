import logging
import re
from datetime import UTC, datetime
from typing import NamedTuple
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
import lxml.html

from newsletter.http import PoliteClient, RobotsDisallowed
from newsletter.ingest.base import FIRST_RUN_LIMIT, FetchResult, RawItem, SourceConfig, domain_of
from newsletter.ingest.extract import fetch_and_extract, parse_datetime

log = logging.getLogger(__name__)

MIN_LINKS = 3
SEEN_CAP = 2000
MAX_FETCH_PER_RUN = 20
CHROME_TAGS = {"nav", "header", "footer", "aside", "dialog", "menu"}
CHROME_ROLES = {"navigation", "menu", "menubar", "tree", "banner", "contentinfo"}
PAGINATION = re.compile(r"/page/\d+/?$")
ASSET = re.compile(r"\.(xml|rss|pdf|png|jpe?g|gif|svg|webp|zip)$", re.I)
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
MONTH_DATE = re.compile(r"(?<![A-Za-z])(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? (\d{1,2}), (\d{4})\b")
ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
MAX_CARD_DEPTH = 8


class PostLink(NamedTuple):
    url: str
    text: str
    published_at: datetime | None


def _in_chrome(ancestors: list) -> bool:
    """Site chrome: inside nav/header/footer/menus, unless also inside an <article> card."""
    if any(el.tag == "article" for el in ancestors):
        return False
    return any(el.tag in CHROME_TAGS or (el.get("role") or "").lower() in CHROME_ROLES
               for el in ancestors)


def _dir(path: str) -> str:
    return path.rstrip("/") + "/"


def _text_date(text: str) -> datetime | None:
    if m := MONTH_DATE.search(text):
        try:
            return datetime(int(m[3]), MONTHS.index(m[1].lower()) + 1, int(m[2]), tzinfo=UTC)
        except ValueError:
            return None
    m = ISO_DATE.search(text)
    return parse_datetime(m[0]) if m else None


def _card_date(a, url_of: dict) -> datetime | None:
    """The date shown in the post's card: the largest ancestor of `a` that links to
    no other post, read from <time datetime>, then its text."""
    card = None
    el = a.getparent()
    for _ in range(MAX_CARD_DEPTH):
        if el is None or {url_of[x] for x in el.iter("a") if x in url_of} != {url_of[a]}:
            break
        card, el = el, el.getparent()
    if card is None:
        return None
    for t in card.iter("time"):
        if found := parse_datetime(t.get("datetime")) or _text_date(t.text_content()):
            return found
    return _text_date(" ".join(" ".join(card.itertext()).split()))


def post_links(html: str, listing_url: str, include: str | None = None) -> list[PostLink]:
    """URL, link text and card date for each post linked from a blog index page, in page order.

    Drops links in site chrome, to other hosts, to the page itself or its parents,
    to pagination, and to copies of the index in other locales. If any links sit in <article> cards, only those are kept.
    `include` is a per-site regex: when set, only links whose path matches it are kept."""
    keep = re.compile(include) if include else None
    try:
        doc = lxml.html.fromstring(html)
    except (ValueError, lxml.etree.ParserError):
        return []
    host = domain_of(listing_url)
    listing_dir = _dir(urlsplit(listing_url).path)
    # the same index in another locale or version, e.g. /us/es/blog/engineering/
    same_index = "/" + listing_dir.strip("/").rsplit("/", 1)[-1] + "/"
    entries: list[tuple[object, str, bool]] = []
    for a in doc.iter("a"):
        href = a.get("href")
        ancestors = list(a.iterancestors())
        if not href or _in_chrome(ancestors):
            continue
        parts = urlsplit(urljoin(listing_url, href.strip()))
        if parts.scheme not in ("http", "https") or domain_of(parts.geturl()) != host:
            continue
        path = _dir(parts.path)
        if listing_dir.startswith(path) or path.endswith(same_index):
            continue
        if PAGINATION.search(parts.path) or ASSET.search(parts.path):
            continue
        if keep is not None and not keep.search(parts.path):
            continue
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
        in_article = any(el.tag == "article" for el in ancestors)
        entries.append((a, url, in_article))
    if any(in_article for _, _, in_article in entries):
        entries = [e for e in entries if e[2]]
    url_of = {a: url for a, url, _ in entries}
    links: dict[str, PostLink] = {}
    for a, url, _ in entries:
        text = " ".join(a.text_content().split())
        if url not in links:
            links[url] = PostLink(url, text, _card_date(a, url_of))
        elif not links[url].text:
            links[url] = links[url]._replace(text=text)
    return list(links.values())


def _slug_title(url: str) -> str:
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return slug.replace("-", " ").replace("_", " ").strip() or url


class ListingAdapter:
    """Reads post links from a blog's index page, for blogs with no feed and no usable sitemap."""

    name = "listing"

    def discover(self, url: str, client: PoliteClient, include: str | None = None) -> SourceConfig | None:
        if include:
            re.compile(include)  # a bad rule fails the seed, not every hourly run
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc or not parts.path.strip("/"):
            return None
        try:
            resp = client.get(url)
        except (httpx.HTTPError, RobotsDisallowed):
            return None
        if resp.status_code != 200:
            return None
        final_url = str(resp.url)
        links = post_links(resp.text, final_url, include)
        if len(links) < MIN_LINKS:
            return None
        host = domain_of(final_url)
        return SourceConfig(
            adapter=self.name,
            config_key=f"listing:{final_url}",
            config={"listing_url": final_url} | ({"include": include} if include else {}),
            title=f"{host}{urlsplit(final_url).path.rstrip('/')}",
            site_domain=host,
            preview_titles=[link.text or _slug_title(link.url) for link in links[:3]],
        )

    def fetch(self, config: dict, state: dict, client: PoliteClient) -> FetchResult:
        first_run = not state
        resp = client.get(config["listing_url"])
        resp.raise_for_status()
        links = post_links(resp.text, str(resp.url), config.get("include"))
        seen = list(state.get("seen", []))
        seen_set = set(seen)
        fresh = [link for link in links if link.url not in seen_set]
        if first_run:
            to_fetch = fresh[:FIRST_RUN_LIMIT]
            seen += [link.url for link in fresh[FIRST_RUN_LIMIT:]]
        else:
            to_fetch = fresh[:MAX_FETCH_PER_RUN]

        items: list[RawItem] = []
        for link in to_fetch:
            try:
                final_url, ex = fetch_and_extract(link.url, client)
            except (httpx.HTTPError, RobotsDisallowed) as exc:
                log.info("page %s failed, will retry: %s", link.url, exc)
                continue
            items.append(RawItem(
                url=final_url,
                title=(ex.title or "").strip() or link.text or _slug_title(link.url),
                author=ex.author,
                # the card's date beats the page's: pages without date metadata get today guessed
                published_at=link.published_at or ex.published_at,
                text=ex.text,
                canonical_link=ex.canonical_link,
                is_teaser=ex.is_teaser,
            ))
            seen.append(link.url)
        return FetchResult(items=items, state={"seen": list(dict.fromkeys(seen))[-SEEN_CAP:]})
