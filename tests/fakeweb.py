"""A tiny URL → response router for httpx.MockTransport."""
from pathlib import Path

import httpx

from newsletter.http import PoliteClient

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeWeb:
    def __init__(self, routes: dict | None = None):
        # url -> bytes | str | httpx.Response | callable(request) -> httpx.Response
        self.routes = dict(routes or {})
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        route = self.routes.get(url)
        if route is None:
            return httpx.Response(404, text="not found")
        if callable(route):
            return route(request)
        if isinstance(route, httpx.Response):
            return route
        return httpx.Response(200, content=route if isinstance(route, bytes) else route.encode())

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests if r.url.path != "/robots.txt"]

    def client(self) -> PoliteClient:
        return PoliteClient(transport=httpx.MockTransport(self), sleep=lambda s: None)


def redirect(to: str) -> httpx.Response:
    return httpx.Response(301, headers={"Location": to})


def rss(entries: list[dict], title: str = "Gen Feed") -> bytes:
    """entries: dicts with id, title, link, date (RFC 822), optional body."""
    items = "".join(
        f"<item><title>{e['title']}</title><link>{e['link']}</link><guid>{e['id']}</guid>"
        f"<pubDate>{e['date']}</pubDate><description>{e.get('body', 'short')}</description></item>"
        for e in entries
    )
    return (f'<?xml version="1.0"?><rss version="2.0"><channel><title>{title}</title>'
            f"<link>https://gen.example.com/</link><description>d</description>{items}"
            "</channel></rss>").encode()
