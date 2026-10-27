import time
from collections.abc import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
BROWSER_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "application/rss+xml,application/atom+xml,*/*;q=0.8"
)


class RobotsDisallowed(Exception):
    pass


class PoliteClient:
    """HTTP client that sends a browser UA, obeys robots.txt, and spaces out
    requests to the same host by `min_interval` seconds."""

    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        min_interval: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = 20.0,
    ):
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": BROWSER_UA, "Accept": BROWSER_ACCEPT},
        )
        self.min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    def _robots_for(self, scheme: str, host: str) -> RobotFileParser | None:
        key = f"{scheme}://{host}"
        if key not in self._robots:
            parser = None
            try:
                resp = self._client.get(f"{key}/robots.txt")
                if resp.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(resp.text.splitlines())
            except httpx.HTTPError:
                parser = None
            self._robots[key] = parser
        return self._robots[key]

    def _throttle(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            wait = self.min_interval - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last[host] = self._clock()

    def get(self, url: str, headers: dict | None = None) -> httpx.Response:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        robots = self._robots_for(parts.scheme, parts.netloc.lower())
        if robots is not None and not robots.can_fetch("*", url):
            raise RobotsDisallowed(url)
        self._throttle(host)
        return self._client.get(url, headers=headers)

    def close(self) -> None:
        self._client.close()
