from datetime import UTC, datetime

import httpx

from newsletter.ingest.sitemap import SitemapAdapter, parse_sitemap
from tests.fakeweb import FakeWeb, fixture

SITE = "https://www.anthropic.com"
POSTS = [f"{SITE}/engineering/post-{i}" for i in range(1, 9)]
CONFIG = {"site": SITE, "sitemaps": [f"{SITE}/sitemap_posts.xml"], "prefix": "/engineering/"}


def page(title, body="Engineers build reliable systems with care and patience. ", n=60):
    return f"<html><head><title>{title}</title></head><body><article><h1>{title}</h1><p>{body * n}</p></article></body></html>"


def site_routes(**extra):
    routes = {
        f"{SITE}/robots.txt": f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap_index.xml\n",
        f"{SITE}/sitemap_index.xml": fixture("sitemap_index.xml"),
        f"{SITE}/sitemap_posts.xml": fixture("sitemap_posts.xml"),
    }
    routes.update({u: page(f"Post {u[-1]}") for u in POSTS})
    routes.update(extra)
    return routes


def test_parse_sitemap_index_and_urlset():
    children, pages = parse_sitemap(fixture("sitemap_index.xml"))
    assert children == [f"{SITE}/sitemap_posts.xml", f"{SITE}/sitemap_pages.xml"]
    assert pages == []
    children, pages = parse_sitemap(fixture("sitemap_posts.xml"))
    assert children == []
    assert len(pages) == 11
    assert pages[0] == (f"{SITE}/engineering/post-3", datetime(2026, 10, 3, 8, tzinfo=UTC))
    assert all(lm.tzinfo is not None for _, lm in pages)


def test_parse_gzip():
    assert parse_sitemap(fixture("sitemap_posts.xml.gz")) == parse_sitemap(fixture("sitemap_posts.xml"))


def test_discover_uses_path_prefix():
    web = FakeWeb(site_routes())
    cfg = SitemapAdapter().discover(f"{SITE}/engineering", web.client())
    assert cfg.adapter == "sitemap"
    assert cfg.config["prefix"] == "/engineering/"
    assert cfg.config["site"] == SITE
    assert cfg.config_key == f"sitemap:{SITE}/engineering/"
    assert cfg.title == "anthropic.com/engineering"
    assert cfg.site_domain == "anthropic.com"
    # only the 8 post URLs count: the first-run fetch sees exactly those
    result = SitemapAdapter().fetch(cfg.config, {}, web.client())
    assert sorted(result.state["seen"]) == sorted(POSTS)


def test_discover_root_path_returns_none():
    web = FakeWeb(site_routes())
    assert SitemapAdapter().discover(f"{SITE}/", web.client()) is None


def test_discover_no_matching_urls_returns_none():
    web = FakeWeb(site_routes())
    assert SitemapAdapter().discover(f"{SITE}/research", web.client()) is None


def test_discover_falls_back_to_sitemap_xml():
    web = FakeWeb({
        f"{SITE}/robots.txt": "User-agent: *\nAllow: /\n",
        f"{SITE}/sitemap.xml": fixture("sitemap_posts.xml"),
    })
    cfg = SitemapAdapter().discover(f"{SITE}/engineering/", web.client())
    assert cfg.config["sitemaps"] == [f"{SITE}/sitemap.xml"]


def test_fetch_first_run_newest_5_by_lastmod():
    web = FakeWeb(site_routes())
    result = SitemapAdapter().fetch(CONFIG, {}, web.client())
    assert sorted(i.title for i in result.items) == [f"Post {i}" for i in range(4, 9)]
    assert sorted(result.state["seen"]) == sorted(POSTS)
    assert len([u for u in web.urls() if u in POSTS]) == 5


def test_fetch_new_url_only():
    new = f"{SITE}/engineering/post-9"
    sitemap = fixture("sitemap_posts.xml").replace(
        b"</urlset>", f"<url><loc>{new}</loc></url></urlset>".encode())
    web = FakeWeb(site_routes(**{f"{SITE}/sitemap_posts.xml": sitemap, new: page("Post 9")}))
    result = SitemapAdapter().fetch(CONFIG, {"seen": list(POSTS)}, web.client())
    assert [i.title for i in result.items] == ["Post 9"]
    assert new in result.state["seen"]


def test_fetch_page_error_not_marked_seen():
    bad = POSTS[7]  # post-8, the newest
    web = FakeWeb(site_routes(**{bad: httpx.Response(500)}))
    result = SitemapAdapter().fetch(CONFIG, {}, web.client())
    assert "Post 8" not in [i.title for i in result.items]
    assert bad not in result.state["seen"]


def test_published_at_falls_back_to_lastmod():
    web = FakeWeb(site_routes())
    result = SitemapAdapter().fetch(CONFIG, {}, web.client())
    item = next(i for i in result.items if i.title == "Post 8")
    assert item.published_at == datetime(2026, 10, 8, 8, tzinfo=UTC)


def test_default_adapters_try_rss_then_sitemap_then_listing():
    from newsletter.ingest.base import default_adapters
    assert [a.name for a in default_adapters()] == ["rss", "sitemap", "listing"]


def _index(children):
    locs = "".join(f"<sitemap><loc>{c}</loc></sitemap>" for c in children)
    return f'<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</sitemapindex>'


def _urlset(urls):
    locs = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'


def test_discover_stores_only_matching_leaf_sitemaps():
    web = FakeWeb(site_routes(**{f"{SITE}/sitemap_pages.xml": _urlset([f"{SITE}/careers/a"])}))
    cfg = SitemapAdapter().discover(f"{SITE}/engineering", web.client())
    assert cfg.config["sitemaps"] == [f"{SITE}/sitemap_posts.xml"]


def test_fetch_walks_only_stored_sitemaps():
    web = FakeWeb(site_routes())
    SitemapAdapter().fetch(CONFIG, {"seen": list(POSTS)}, web.client())
    assert [u for u in web.urls() if "sitemap" in u] == [f"{SITE}/sitemap_posts.xml"]


def test_discover_walk_is_bounded_and_tries_relevant_children_first():
    from newsletter.ingest.sitemap import MAX_DISCOVER_FETCHES
    junk = [f"{SITE}/sitemap-city-{i}.xml" for i in range(500)]
    blog = f"{SITE}/sitemap-blog-engineering.xml"
    routes = {f"{SITE}/robots.txt": f"Sitemap: {SITE}/sitemap.xml\n",
              f"{SITE}/sitemap.xml": _index(junk + [blog]),
              blog: _urlset([f"{SITE}/blog/engineering/post-{i}" for i in range(3)])}
    routes.update({j: _urlset([f"{SITE}/city/{i}"]) for i, j in enumerate(junk)})
    web = FakeWeb(routes)
    cfg = SitemapAdapter().discover(f"{SITE}/blog/engineering", web.client())
    assert cfg.config["sitemaps"] == [blog]
    assert len([u for u in web.urls() if "sitemap" in u]) <= MAX_DISCOVER_FETCHES


def test_fetch_caps_new_urls_per_run():
    from newsletter.ingest.sitemap import MAX_FETCH_PER_RUN
    new = [f"{SITE}/engineering/new-{i}" for i in range(MAX_FETCH_PER_RUN + 10)]
    sitemap = _urlset(POSTS + new)
    web = FakeWeb(site_routes(**{f"{SITE}/sitemap_posts.xml": sitemap, **{u: page(u[-6:]) for u in new}}))
    result = SitemapAdapter().fetch(CONFIG, {"seen": list(POSTS)}, web.client())
    assert len(result.items) == MAX_FETCH_PER_RUN
    assert len(set(new) - set(result.state["seen"])) == 10  # the rest wait for later runs


def test_fetch_marks_old_lastmod_seen_without_fetching():
    old = f"{SITE}/engineering/ancient"
    sitemap = fixture("sitemap_posts.xml").replace(
        b"</urlset>", f"<url><loc>{old}</loc><lastmod>2020-01-01</lastmod></url></urlset>".encode())
    web = FakeWeb(site_routes(**{f"{SITE}/sitemap_posts.xml": sitemap, old: page("Ancient")}))
    result = SitemapAdapter().fetch(CONFIG, {"seen": list(POSTS)}, web.client())
    assert result.items == []
    assert old in result.state["seen"]
    assert old not in web.urls()
