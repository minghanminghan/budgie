from dataclasses import dataclass
from datetime import UTC, datetime

import lxml.html
import trafilatura

from newsletter.http import PoliteClient

TEASER_MARKERS = ('"audience":"only_paid"', 'class="paywall', "Member-only story")
DATE_META = ("article:published_time", "og:article:published_time", "datePublished")


@dataclass
class Extracted:
    title: str | None
    author: str | None
    published_at: datetime | None
    text: str
    canonical_link: str | None
    is_teaser: bool


def word_count(text: str) -> int:
    return len(text.split())


def looks_like_teaser(html: str, text: str) -> bool:
    return any(m in html for m in TEASER_MARKERS) and word_count(text) < 600


def parse_datetime(value: str | None) -> datetime | None:
    """ISO date or datetime → aware UTC. Naive values are taken as UTC."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _head_values(html: str) -> tuple[str | None, str | None]:
    try:
        doc = lxml.html.fromstring(html)
    except (ValueError, lxml.etree.ParserError):
        return None, None
    canonical = next(iter(doc.xpath('//link[@rel="canonical"]/@href')), None)
    published = None
    for name in DATE_META:
        found = doc.xpath(f'//meta[@property="{name}" or @name="{name}" or @itemprop="{name}"]/@content')
        if found:
            published = found[0]
            break
    return canonical, published


def extract_page(html: str, url: str) -> Extracted:
    doc = trafilatura.bare_extraction(html, url=url, with_metadata=True)
    canonical, published = _head_values(html)
    text = (doc.text if doc else None) or ""
    return Extracted(
        title=doc.title if doc else None,
        author=doc.author if doc else None,
        published_at=parse_datetime(published) or parse_datetime(doc.date if doc else None),
        text=text,
        canonical_link=canonical,
        is_teaser=looks_like_teaser(html, text),
    )


def fetch_and_extract(url: str, client: PoliteClient) -> tuple[str, Extracted]:
    resp = client.get(url)
    resp.raise_for_status()
    final_url = str(resp.url)
    return final_url, extract_page(resp.text, final_url)
