from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from urllib.parse import urlsplit

from newsletter.http import PoliteClient

FIRST_RUN_LIMIT = 5


@dataclass
class RawItem:
    url: str
    title: str
    author: str | None
    published_at: datetime | None
    text: str
    canonical_link: str | None = None
    is_teaser: bool = False


@dataclass
class SourceConfig:
    adapter: str
    config_key: str
    config: dict
    title: str
    site_domain: str
    preview_titles: list[str] = field(default_factory=list)


@dataclass
class FetchResult:
    items: list[RawItem]
    state: dict


class Adapter(Protocol):
    name: str

    def discover(self, url: str, client: PoliteClient) -> SourceConfig | None: ...

    def fetch(self, config: dict, state: dict, client: PoliteClient) -> FetchResult: ...


def domain_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def default_adapters() -> list[Adapter]:
    from newsletter.ingest.rss import RssAdapter
    from newsletter.ingest.sitemap import SitemapAdapter

    return [RssAdapter(), SitemapAdapter()]


def discover_source(url: str, client: PoliteClient, adapters: list[Adapter]) -> SourceConfig | None:
    """Try each adapter in order; the first one that can read `url` wins."""
    for adapter in adapters:
        found = adapter.discover(url, client)
        if found is not None:
            return found
    return None
