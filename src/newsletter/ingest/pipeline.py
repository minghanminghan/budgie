import logging
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from newsletter.ingest.base import Adapter, RawItem, domain_of
from newsletter.ingest.extract import word_count
from newsletter.models import Item, ItemSource, Source
from newsletter.urls import canonicalize_url, dedup_key

log = logging.getLogger(__name__)

ERROR_MAX = 500


def _published(value: datetime | None, now: datetime) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return min(value, now)


def _canonical(raw: RawItem) -> str:
    # A canonical link that points at a site root (a common CMS mistake) would
    # merge every post into one item, so it is ignored.
    link = raw.canonical_link
    if link and urlsplit(link).path.strip("/") == "" and urlsplit(raw.url).path.strip("/") != "":
        link = None
    return canonicalize_url(raw.url, link)


def _store(session: Session, source: Source, raw: RawItem, now: datetime) -> tuple[Item, bool]:
    canonical = _canonical(raw)
    key = dedup_key(raw.title, raw.author)
    item = session.scalar(select(Item).where(Item.canonical_url == canonical))
    if item is None and key is not None:
        item = session.scalar(select(Item).where(Item.dedup_key == key).order_by(Item.id).limit(1))
    created = item is None
    if created:
        item = Item(
            canonical_url=canonical,
            url=raw.url,
            title=raw.title,
            author=raw.author,
            site_domain=domain_of(canonical),
            published_at=_published(raw.published_at, now),
            first_seen_at=now,
            text=raw.text,
            word_count=word_count(raw.text),
            dedup_key=key,
            is_teaser=raw.is_teaser,
        )
        session.add(item)
        session.flush()
    if session.get(ItemSource, (item.id, source.id)) is None:
        session.add(ItemSource(item_id=item.id, source_id=source.id, seen_at=now, raw_url=raw.url))
        session.flush()
    return item, created


def store_raw_item(session: Session, source: Source, raw: RawItem, now: datetime) -> Item:
    return _store(session, source, raw, now)[0]


def ingest_source(session: Session, source: Source, client, adapters: dict[str, Adapter],
                  now: datetime) -> int:
    adapter = adapters[source.adapter]
    result = adapter.fetch(dict(source.config), dict(source.state or {}), client)
    new = sum(_store(session, source, raw, now)[1] for raw in result.items)
    source.state = result.state
    source.last_fetched_at = now
    source.last_error = None
    session.flush()
    return new


def ingest_all(session: Session, client, adapters: dict[str, Adapter],
               now: datetime) -> dict[int, int | str]:
    """Ingest every active source. One failing source never stops the run."""
    ids = session.scalars(select(Source.id).where(Source.status == "active").order_by(Source.id)).all()
    results: dict[int, int | str] = {}
    for source_id in ids:
        source = session.get(Source, source_id)
        try:
            results[source_id] = ingest_source(session, source, client, adapters, now)
            session.commit()
        except Exception as exc:
            session.rollback()
            message = f"{type(exc).__name__}: {exc}"[:ERROR_MAX]
            log.warning("source %s (%s) failed: %s", source_id, source.title, message)
            source = session.get(Source, source_id)
            source.last_error = message
            source.last_fetched_at = now
            session.commit()
            results[source_id] = message
    return results
