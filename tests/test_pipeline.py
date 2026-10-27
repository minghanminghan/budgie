from datetime import timedelta

import httpx
from sqlalchemy import func, select

from newsletter.ingest.pipeline import ingest_all, ingest_source, store_raw_item
from newsletter.models import Item, ItemSource
from tests.fakes import FakeAdapter, raw


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_store_new_item_sets_fields(session, make_source, now):
    src = make_source()
    item = store_raw_item(session, src, raw("https://www.a.com/p/?utm_source=x", text="one two three",
                                            is_teaser=True, author="Jane"), now)
    assert item.canonical_url == "https://a.com/p"
    assert item.url == "https://www.a.com/p/?utm_source=x"
    assert item.word_count == 3
    assert item.is_teaser is True
    assert item.site_domain == "a.com"
    assert item.first_seen_at == now
    assert item.dedup_key == "a post|jane"


def test_duplicate_via_two_sources_links_item(session, make_source, now):
    a, b = make_source(), make_source()
    i1 = store_raw_item(session, a, raw("https://www.a.com/p?utm_source=x"), now)
    i2 = store_raw_item(session, b, raw("https://a.com/p"), now)
    assert i1.id == i2.id
    assert count(session, Item) == 1
    assert count(session, ItemSource) == 2


def test_same_source_twice_one_link(session, make_source, now):
    a = make_source()
    store_raw_item(session, a, raw("https://a.com/p"), now)
    store_raw_item(session, a, raw("https://a.com/p"), now)
    assert count(session, ItemSource) == 1


def test_cross_post_dedup_by_title_author(session, make_source, now):
    a, b = make_source(), make_source()
    i1 = store_raw_item(session, a, raw("https://medium.com/x/my-post-1a2b", title="My Post!", author="Jane Doe"), now)
    i2 = store_raw_item(session, b, raw("https://jane.dev/my-post", title="my post", author="jane doe"), now)
    assert i1.id == i2.id
    assert count(session, Item) == 1


def test_no_author_no_title_dedup(session, make_source, now):
    a = make_source()
    store_raw_item(session, a, raw("https://a.com/1", title="Same"), now)
    store_raw_item(session, a, raw("https://b.com/2", title="Same"), now)
    assert count(session, Item) == 2


def test_root_canonical_link_ignored(session, make_source, now):
    a = make_source()
    i1 = store_raw_item(session, a, raw("https://a.com/p1", canonical_link="https://a.com/"), now)
    i2 = store_raw_item(session, a, raw("https://a.com/p2", canonical_link="https://a.com/"), now)
    assert i1.id != i2.id
    assert i1.canonical_url == "https://a.com/p1"


def test_published_at_normalization(session, make_source, now):
    src = make_source()
    naive = store_raw_item(session, src, raw("https://a.com/1", published_at=(now - timedelta(hours=2)).replace(tzinfo=None)), now)
    assert naive.published_at == now - timedelta(hours=2)
    assert naive.published_at.tzinfo is not None
    future = store_raw_item(session, src, raw("https://a.com/2", published_at=now + timedelta(days=1)), now)
    assert future.published_at == now
    missing = store_raw_item(session, src, raw("https://a.com/3", published_at=None), now)
    assert missing.published_at is None


def test_ingest_source_updates_state_and_timestamps(session, make_source, now):
    src = make_source(adapter="fake", config={"url": "u"}, last_error="old")
    adapter = FakeAdapter(items={"u": [raw("https://a.com/1"), raw("https://a.com/2")]})
    n = ingest_source(session, src, None, {"fake": adapter}, now)
    assert n == 2
    assert src.state == {"seen": ["https://a.com/1", "https://a.com/2"], "n": 1}
    assert src.last_fetched_at == now
    assert src.last_error is None
    assert ingest_source(session, src, None, {"fake": adapter}, now) == 0
    assert adapter.fetch_calls[1][1] == {"seen": ["https://a.com/1", "https://a.com/2"], "n": 1}


def test_ingest_all_isolates_failures(session, make_source, now):
    a = make_source(adapter="fake", config={"url": "a"})
    b = make_source(adapter="fake", config={"url": "b"})
    session.commit()
    adapter = FakeAdapter(items={"a": httpx.ReadTimeout("timed out"), "b": [raw("https://b.com/1")]})
    result = ingest_all(session, None, {"fake": adapter}, now)
    assert isinstance(result[a.id], str) and "timed out" in result[a.id]
    assert result[b.id] == 1
    session.expire_all()
    assert "timed out" in session.get(type(a), a.id).last_error
    assert session.get(type(b), b.id).last_error is None
    assert count(session, Item) == 1


def test_ingest_all_truncates_error(session, make_source, now):
    a = make_source(adapter="fake", config={"url": "a"})
    session.commit()
    ingest_all(session, None, {"fake": FakeAdapter(items={"a": ValueError("x" * 2000)})}, now)
    assert len(session.get(type(a), a.id).last_error) == 500


def test_ingest_all_skips_unsubscribed(session, make_source, now):
    a = make_source(adapter="fake", config={"url": "a"}, status="unsubscribed")
    adapter = FakeAdapter(items={"a": [raw("https://a.com/1")]})
    assert ingest_all(session, None, {"fake": adapter}, now) == {}
    assert adapter.fetch_calls == []
