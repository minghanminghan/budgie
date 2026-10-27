import httpx
import pytest

from newsletter.http import BROWSER_UA, PoliteClient, RobotsDisallowed


class FakeTime:
    def __init__(self):
        self.t = 100.0
        self.sleeps: list[float] = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)


def make_client(handler, ft=None):
    ft = ft or FakeTime()
    return PoliteClient(transport=httpx.MockTransport(handler), sleep=ft.sleep, clock=ft.clock), ft


def ok_handler(robots="", robots_status=200, hits=None):
    def handler(request: httpx.Request):
        if request.url.path == "/robots.txt":
            if hits is not None:
                hits.append(request.url.host)
            return httpx.Response(robots_status, text=robots)
        return httpx.Response(200, text="ok")
    return handler


def test_sends_browser_user_agent():
    seen = []

    def handler(request):
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, text="")

    client, _ = make_client(handler)
    client.get("https://a.com/x")
    assert seen and all(ua == BROWSER_UA for ua in seen)


def test_robots_disallow_raises():
    client, _ = make_client(ok_handler("User-agent: *\nDisallow: /private"))
    with pytest.raises(RobotsDisallowed):
        client.get("https://a.com/private/x")


def test_robots_404_allows_all():
    client, _ = make_client(ok_handler("Disallow: /", robots_status=404))
    assert client.get("https://a.com/private/x").status_code == 200


def test_same_host_is_throttled():
    client, ft = make_client(ok_handler())
    client.get("https://a.com/1")
    client.get("https://a.com/2")
    assert ft.sleeps == [1.0]


def test_other_host_not_throttled():
    client, ft = make_client(ok_handler())
    client.get("https://a.com/1")
    client.get("https://b.com/1")
    assert ft.sleeps == []


def test_robots_fetched_once_per_host():
    hits = []
    client, _ = make_client(ok_handler(hits=hits))
    for i in range(3):
        client.get(f"https://a.com/{i}")
    assert hits == ["a.com"]
