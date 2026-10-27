"""Fake adapters for pipeline, sources, and web tests (no network)."""
from newsletter.ingest.base import FetchResult, RawItem, SourceConfig


def raw(url, title="A Post", author=None, published_at=None, text="word " * 300, **kw):
    return RawItem(url=url, title=title, author=author, published_at=published_at, text=text, **kw)


def config_for(url: str, title: str = "Fake Blog") -> SourceConfig:
    return SourceConfig(adapter="fake", config_key=f"fake:{url}", config={"url": url},
                        title=title, site_domain="fake.example.com",
                        preview_titles=["One", "Two", "Three"])


class FakeAdapter:
    """discover() knows the URLs in `known`; fetch() returns `items[config url]`
    or raises the exception stored there."""

    name = "fake"

    def __init__(self, known=(), items=None):
        self.known = set(known)
        self.items = items or {}
        self.fetch_calls: list[tuple[dict, dict]] = []

    def discover(self, url, client):
        return config_for(url) if url in self.known else None

    def fetch(self, config, state, client):
        self.fetch_calls.append((config, state))
        got = self.items.get(config.get("url") or config.get("feed_url"), [])
        if isinstance(got, Exception):
            raise got
        return FetchResult(items=list(got), state={"seen": [i.url for i in got], "n": len(self.fetch_calls)})
