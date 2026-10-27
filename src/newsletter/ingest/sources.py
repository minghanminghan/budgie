import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from newsletter.ingest.base import Adapter, SourceConfig, discover_source
from newsletter.models import Source

log = logging.getLogger(__name__)


def preview(url: str, client, adapters: list[Adapter]) -> SourceConfig | None:
    url = url.strip()
    if urlsplit(url).scheme not in ("http", "https"):
        return None
    return discover_source(url, client, adapters)


def subscribe(session: Session, url: str, client, adapters: list[Adapter],
              now: datetime) -> Source | None:
    """Discover and subscribe. The first ingest happens on the next hourly run."""
    cfg = preview(url, client, adapters)
    if cfg is None:
        return None
    source = session.scalar(select(Source).where(Source.config_key == cfg.config_key))
    if source is not None:
        source.status = "active"
        source.unsubscribed_at = None
    else:
        source = Source(
            adapter=cfg.adapter,
            input_url=url.strip(),
            config_key=cfg.config_key,
            config=cfg.config,
            title=cfg.title,
            site_domain=cfg.site_domain,
            state={},
            status="active",
            created_at=now,
        )
        session.add(source)
    session.commit()
    return source


def unsubscribe(session: Session, source_id: int, now: datetime) -> bool:
    source = session.get(Source, source_id)
    if source is None:
        return False
    if source.status != "unsubscribed":
        source.status = "unsubscribed"
        source.unsubscribed_at = now
        session.commit()
    return True


def seed_from_yaml(session: Session, path: Path, client, adapters: list[Adapter],
                   now: datetime) -> list[tuple[str, Source | None]]:
    urls = (yaml.safe_load(Path(path).read_text()) or {}).get("sources") or []
    out: list[tuple[str, Source | None]] = []
    for url in urls:
        try:
            out.append((url, subscribe(session, url, client, adapters, now)))
        except Exception:
            session.rollback()
            log.exception("seed failed for %s", url)
            out.append((url, None))
    return out
