from datetime import UTC, datetime
from itertools import count

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from newsletter.models import Base, Item, ItemSource, Source

NOW = datetime(2026, 10, 27, 10, 30, tzinfo=UTC)


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture
def now():
    return NOW


@pytest.fixture
def make_source(session):
    n = count(1)

    def _make(**kw):
        i = next(n)
        feed = kw.pop("feed_url", f"https://blog{i}.example.com/feed")
        defaults = dict(
            adapter="rss",
            input_url=feed,
            config={"feed_url": feed},
            config_key=f"rss:{feed}",
            title=f"Blog {i}",
            site_domain=f"blog{i}.example.com",
            state={},
            status="active",
            created_at=NOW,
        )
        defaults.update(kw)
        src = Source(**defaults)
        session.add(src)
        session.flush()
        return src

    return _make


@pytest.fixture
def make_item(session):
    n = count(1)

    def _make(source, **kw):
        i = next(n)
        url = kw.pop("canonical_url", f"https://{source.site_domain}/post-{i}")
        defaults = dict(
            canonical_url=url,
            url=url,
            title=f"Post {i}",
            author=None,
            site_domain=source.site_domain,
            published_at=NOW,
            first_seen_at=NOW,
            text="body " * 500,
            word_count=500,
            dedup_key=None,
            is_teaser=False,
        )
        defaults.update(kw)
        item = Item(**defaults)
        session.add(item)
        session.flush()
        session.add(ItemSource(item_id=item.id, source_id=source.id, seen_at=NOW, raw_url=url))
        session.flush()
        return item

    return _make
