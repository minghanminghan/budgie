import httpx

from newsletter.ingest.base import FIRST_RUN_LIMIT
from newsletter.ingest.extract import word_count
from newsletter.ingest.rss import RssAdapter
from tests.fakeweb import FakeWeb, fixture, redirect, rss


def entries(n):
    # entry i is published on day i, so higher i is newer
    return [dict(id=f"e{i}", title=f"Entry {i}", link=f"https://gen.example.com/p/{i}",
                 date=f"Thu, {i:02d} Oct 2026 10:00:00 GMT", body="word " * 250)
            for i in range(1, n + 1)]


def test_discover_url_is_feed():
    url = "https://blog.example.com/rss.xml"
    web = FakeWeb({url: fixture("rss_full.xml")})
    cfg = RssAdapter().discover(url, web.client())
    assert cfg.config == {"feed_url": url}
    assert cfg.config_key == "rss:" + url
    assert cfg.adapter == "rss"
    assert cfg.title == "Example Engineering"


def test_discover_link_alternate():
    web = FakeWeb({
        "https://blog.example.com/": fixture("blog_home.html"),
        "https://blog.example.com/feed.xml": fixture("rss_full.xml"),
    })
    cfg = RssAdapter().discover("https://blog.example.com/", web.client())
    assert cfg.config["feed_url"] == "https://blog.example.com/feed.xml"


def test_discover_substack_profile():
    web = FakeWeb({
        "https://pragmaticengineer.substack.com/feed": redirect("https://newsletter.pragmaticengineer.com/feed"),
        "https://newsletter.pragmaticengineer.com/feed": fixture("rss_full.xml"),
    })
    cfg = RssAdapter().discover("https://substack.com/@pragmaticengineer", web.client())
    assert "https://pragmaticengineer.substack.com/feed" in web.urls()
    assert cfg.config["feed_url"] == "https://newsletter.pragmaticengineer.com/feed"


def test_discover_medium_pub():
    web = FakeWeb({"https://medium.com/feed/pinterest-engineering": fixture("rss_full.xml")})
    cfg = RssAdapter().discover("https://medium.com/pinterest-engineering", web.client())
    assert cfg.config["feed_url"] == "https://medium.com/feed/pinterest-engineering"


def test_discover_common_path_from_article_url():
    web = FakeWeb({
        "https://site.com/blog/a-post": "<html><body><p>No links here.</p></body></html>",
        "https://site.com/feed": fixture("rss_full.xml"),
    })
    cfg = RssAdapter().discover("https://site.com/blog/a-post", web.client())
    assert cfg.config["feed_url"] == "https://site.com/feed"


def test_discover_rejects_empty_feed():
    web = FakeWeb({"https://www.engineering.fyi/feed.xml": fixture("empty_feed.xml")})
    assert RssAdapter().discover("https://www.engineering.fyi/feed.xml", web.client()) is None


def test_discover_preview_titles():
    url = "https://blog.example.com/rss.xml"
    web = FakeWeb({url: fixture("rss_full.xml")})
    cfg = RssAdapter().discover(url, web.client())
    assert cfg.preview_titles == ["Full Post 1", "Full Post 2", "Full Post 3"]


def test_fetch_first_run_limit():
    url = "https://gen.example.com/feed"
    web = FakeWeb({url: rss(entries(8))})
    result = RssAdapter().fetch({"feed_url": url}, {}, web.client())
    assert len(result.items) == FIRST_RUN_LIMIT
    assert sorted(i.title for i in result.items) == [f"Entry {i}" for i in range(4, 9)]
    assert len(result.state["seen"]) == 8


def test_fetch_skips_seen_and_sends_etag():
    url = "https://gen.example.com/feed"
    web = FakeWeb({url: httpx.Response(200, content=rss(entries(3)), headers={"ETag": '"v1"'})})
    adapter, client = RssAdapter(), web.client()
    first = adapter.fetch({"feed_url": url}, {}, client)
    second = adapter.fetch({"feed_url": url}, first.state, client)
    assert second.items == []
    assert web.requests[-1].headers["if-none-match"] == '"v1"'


def test_fetch_304_returns_no_items():
    url = "https://gen.example.com/feed"
    web = FakeWeb({url: httpx.Response(304)})
    state = {"etag": '"v1"', "modified": None, "seen": ["e1"]}
    result = RssAdapter().fetch({"feed_url": url}, state, web.client())
    assert result.items == []
    assert result.state["seen"] == ["e1"]


def test_fetch_uses_feed_full_text():
    url = "https://blog.example.com/feed.xml"
    web = FakeWeb({url: fixture("rss_full.xml")})
    result = RssAdapter().fetch({"feed_url": url}, {}, web.client())
    assert web.urls() == [url]
    assert result.items and all(word_count(i.text) >= 200 for i in result.items)
    assert result.items[0].author == "Jane Doe"
    assert result.items[0].published_at.tzinfo is not None


def test_fetch_short_summary_fetches_page():
    url = "https://summary.example.com/feed"
    page = "https://summary.example.com/posts/article"
    web = FakeWeb({url: fixture("rss_summary.xml"), page: fixture("article.html")})
    result = RssAdapter().fetch({"feed_url": url}, {}, web.client())
    assert page in web.urls()
    (item,) = result.items
    assert "Distributed systems fail" in item.text
    assert item.canonical_link == "https://summary.example.com/posts/how-we-scaled"
    assert item.author == "Ada Lovelace"
