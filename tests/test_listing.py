import re
from datetime import UTC, datetime

import httpx
import pytest

from newsletter.ingest.listing import MAX_FETCH_PER_RUN, ListingAdapter, post_links
from tests.fakeweb import FakeWeb

ANTH = "https://www.anthropic.com"
UBER = "https://www.uber.com"
UBER_LIST = f"{UBER}/us/en/blog/engineering/"


def page(title, body="Engineers build reliable systems with care and patience. ", n=60):
    return f"<html><head><title>{title}</title></head><body><article><h1>{title}</h1><p>{body * n}</p></article></body></html>"


def card(href, title, day=1):
    return f'<article><time>Oct {day}, 2026</time><div><h3><a href="{href}">{title}</a></h3></div></article>'


def anthropic_listing(slugs):
    """Shaped like anthropic.com/engineering: posts live under /research/, in <article> cards;
    a hero carousel and a team list link elsewhere outside any <article>."""
    cards = "".join(card(f"/research/{s}", s.replace("-", " ").title(), day=20 - i) for i, s in enumerate(slugs))
    return f"""<html><body>
      <header><nav><a href="/">Home</a><a href="/news">News</a><a href="/engineering">Engineering</a></nav></header>
      <main>
        <div class="carousel"><a href="/research/{slugs[0]}">Read more</a><a href="/engineering">Engineering</a></div>
        <div class="cards">{cards}</div>
        <div class="teams"><a href="/research/team/alignment"><h3>Alignment</h3></a>
          <a href="/research/team/economics"><h3>Economics</h3></a></div>
      </main>
      <footer><a href="/careers">Careers</a></footer></body></html>"""


def uber_listing(slugs, next_page=True):
    """Shaped like uber.com/us/en/blog/engineering/: no <article>; category menus sit in
    role=navigation / role=tree; each post is linked twice (title + image)."""
    menu = "".join(f'<li><a href="{UBER}/us/en/blog/{c}/">{c.title()}Learn more</a></li>'
                   for c in ("ride", "eat", "earn"))
    topics = "".join(f'<li><a href="{UBER_LIST}{t}/">{t}</a></li>' for t in ("backend", "data"))
    posts = "".join(f'<div><div>Backend</div><a href="{UBER}/us/en/blog/{s}/">{s.replace("-", " ").title()}</a>'
                    f'<div>September {20 - i}, 2026</div>'
                    f'<a href="{UBER}/us/en/blog/{s}/"><img src="x.png"></a></div>' for i, s in enumerate(slugs))
    nxt = f'<a href="{UBER_LIST}page/2/">Next</a>' if next_page else ""
    return f"""<html><body><main>
      <div role="navigation"><a href="{UBER}/us/en/blog/">Uber Blog</a><ul>{menu}</ul></div>
      <nav><ul>{topics}</ul></nav>
      <a href="/us/es/blog/engineering/?id=1">Español</a><a href="/us/en/blog/engineering/?id=1">English</a>
      <div class="grid">{posts}</div>{nxt}
      <ul role="tree"><li role="treeitem"><a href="{UBER}/us/en/blog/freight/">Freight</a></li></ul>
      <a href="https://twitter.com/uber">Twitter</a>
      </main></body></html>"""


ANTH_SLUGS = [f"post-{i}" for i in range(1, 9)]
UBER_SLUGS = [f"uber-post-{i}" for i in range(1, 9)]


def anth_routes(slugs=ANTH_SLUGS, **extra):
    routes = {f"{ANTH}/engineering": anthropic_listing(slugs)}
    routes.update({f"{ANTH}/research/{s}": page(f"Anth {s}") for s in slugs})
    routes.update(extra)
    return routes


def uber_routes(slugs=UBER_SLUGS, **extra):
    routes = {UBER_LIST: uber_listing(slugs)}
    routes.update({f"{UBER}/us/en/blog/{s}/": page(f"Uber {s}") for s in slugs})
    routes.update(extra)
    return routes


def test_post_links_anthropic_keeps_only_article_cards():
    links = post_links(anthropic_listing(ANTH_SLUGS), f"{ANTH}/engineering")
    assert [link.url for link in links] == [f"{ANTH}/research/{s}" for s in ANTH_SLUGS]
    assert links[0].text == "Post 1"
    assert links[0].published_at == datetime(2026, 10, 20, tzinfo=UTC)


def test_post_links_uber_drops_menus_parents_pagination_and_other_hosts():
    links = post_links(uber_listing(UBER_SLUGS), UBER_LIST)
    assert [link.url for link in links] == [f"{UBER}/us/en/blog/{s}/" for s in UBER_SLUGS]
    assert links[0].text == "Uber Post 1"  # the title link's text, not the empty image link
    assert links[1].published_at == datetime(2026, 9, 19, tzinfo=UTC)


def test_post_links_keeps_header_inside_article():
    html = '<main><article><header><h2><a href="/blog/a">A</a></h2></header></article></main>'
    assert post_links(html, "https://x.example.com/blog") == [("https://x.example.com/blog/a", "A", None)]


def test_post_links_date_only_from_own_card():
    html = ('<main><div><a href="/blog/a">A</a><span>2026-10-02</span></div>'
            '<div><a href="/blog/b">B</a></div></main>')
    links = post_links(html, "https://x.example.com/blog")
    assert [link.published_at for link in links] == [datetime(2026, 10, 2, tzinfo=UTC), None]


def test_discover_anthropic():
    web = FakeWeb(anth_routes())
    cfg = ListingAdapter().discover(f"{ANTH}/engineering", web.client())
    assert cfg.adapter == "listing"
    assert cfg.config == {"listing_url": f"{ANTH}/engineering"}
    assert cfg.config_key == f"listing:{ANTH}/engineering"
    assert cfg.title == "anthropic.com/engineering"
    assert cfg.site_domain == "anthropic.com"
    assert cfg.preview_titles == ["Post 1", "Post 2", "Post 3"]


def test_discover_uses_final_url_after_redirect():
    web = FakeWeb(uber_routes(**{UBER_LIST.rstrip("/"): httpx.Response(301, headers={"Location": UBER_LIST})}))
    cfg = ListingAdapter().discover(UBER_LIST.rstrip("/"), web.client())
    assert cfg.config == {"listing_url": UBER_LIST}


def test_discover_too_few_links_returns_none():
    web = FakeWeb(anth_routes(slugs=["only-one", "two"]))
    assert ListingAdapter().discover(f"{ANTH}/engineering", web.client()) is None


def test_discover_bad_page_returns_none():
    web = FakeWeb({})
    assert ListingAdapter().discover(f"{ANTH}/engineering", web.client()) is None
    assert ListingAdapter().discover(f"{ANTH}/", web.client()) is None


def test_fetch_first_run_takes_first_5_in_page_order():
    web = FakeWeb(uber_routes())
    result = ListingAdapter().fetch({"listing_url": UBER_LIST}, {}, web.client())
    assert [i.title for i in result.items] == [f"Uber uber-post-{i}" for i in range(1, 6)]
    assert sorted(result.state["seen"]) == sorted(f"{UBER}/us/en/blog/{s}/" for s in UBER_SLUGS)
    assert len([u for u in web.urls() if u != UBER_LIST]) == 5


def test_fetch_prefers_card_date_over_page_guess():
    web = FakeWeb(uber_routes())
    result = ListingAdapter().fetch({"listing_url": UBER_LIST}, {}, web.client())
    assert result.items[0].published_at == datetime(2026, 9, 20, tzinfo=UTC)


def test_fetch_new_link_only():
    seen = [f"{ANTH}/research/{s}" for s in ANTH_SLUGS]
    web = FakeWeb(anth_routes(slugs=["fresh"] + ANTH_SLUGS))
    result = ListingAdapter().fetch({"listing_url": f"{ANTH}/engineering"}, {"seen": seen}, web.client())
    assert [i.title for i in result.items] == ["Anth fresh"]
    assert f"{ANTH}/research/fresh" in result.state["seen"]


def test_fetch_page_error_not_marked_seen():
    bad = f"{UBER}/us/en/blog/uber-post-1/"
    web = FakeWeb(uber_routes(**{bad: httpx.Response(500)}))
    result = ListingAdapter().fetch({"listing_url": UBER_LIST}, {}, web.client())
    assert "Uber uber-post-1" not in [i.title for i in result.items]
    assert bad not in result.state["seen"]


def test_fetch_title_falls_back_to_link_text():
    url = f"{ANTH}/research/post-1"
    web = FakeWeb(anth_routes(**{url: "<html><body><p>no title here</p></body></html>"}))
    result = ListingAdapter().fetch({"listing_url": f"{ANTH}/engineering"}, {}, web.client())
    assert result.items[0].title == "Post 1"


def test_fetch_listing_error_raises():
    web = FakeWeb({UBER_LIST: httpx.Response(503)})
    with pytest.raises(httpx.HTTPStatusError):
        ListingAdapter().fetch({"listing_url": UBER_LIST}, {"seen": []}, web.client())


def test_fetch_caps_new_links_per_run():
    slugs = [f"n-{i}" for i in range(MAX_FETCH_PER_RUN + 5)]
    web = FakeWeb(uber_routes(slugs=slugs))
    result = ListingAdapter().fetch({"listing_url": UBER_LIST}, {"seen": ["x"]}, web.client())
    assert len(result.items) == MAX_FETCH_PER_RUN
    assert len([s for s in slugs if f"{UBER}/us/en/blog/{s}/" not in result.state["seen"]]) == 5


def test_post_links_include_keeps_only_matching_paths():
    html = '<main><a href="/research/a">A</a><a href="/news/b">B</a><a href="/research/team/c">C</a></main>'
    links = post_links(html, "https://x.example.com/engineering", include=r"^/research/(?!team/)")
    assert [link.url for link in links] == ["https://x.example.com/research/a"]


def test_discover_with_include_stores_it():
    web = FakeWeb(anth_routes())
    cfg = ListingAdapter().discover(f"{ANTH}/engineering", web.client(), include=r"^/research/post-[1-4]$")
    assert cfg.config == {"listing_url": f"{ANTH}/engineering", "include": r"^/research/post-[1-4]$"}
    result = ListingAdapter().fetch(cfg.config, {}, web.client())
    assert sorted(result.state["seen"]) == [f"{ANTH}/research/post-{i}" for i in range(1, 5)]


def test_discover_bad_include_raises():
    with pytest.raises(re.error):
        ListingAdapter().discover(f"{ANTH}/engineering", FakeWeb(anth_routes()).client(), include="(")
