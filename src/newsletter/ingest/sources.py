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


def preview(url: str, client, adapters: list[Adapter], adapter: str | None = None,
            options: dict | None = None) -> SourceConfig | None:
    """`adapter` pins discovery to one adapter by name instead of trying each in order.
    `options` (e.g. a listing `include` rule) are for that adapter, so they need the pin."""
    if options and adapter is None:
        raise ValueError(f"options {sorted(options)} need a pinned adapter")
    url = url.strip()
    if urlsplit(url).scheme not in ("http", "https"):
        return None
    if adapter is not None:
        adapters = [a for a in adapters if a.name == adapter]
    return discover_source(url, client, adapters, options)


def subscribe(session: Session, url: str, client, adapters: list[Adapter],
              now: datetime, adapter: str | None = None, options: dict | None = None,
              reactivate: bool = True) -> Source | None:
    """Discover and subscribe. The first ingest happens on the next hourly run.
    With `reactivate=False`, an unsubscribed source stays unsubscribed."""
    cfg = preview(url, client, adapters, adapter, options)
    if cfg is None:
        return None
    source = session.scalar(select(Source).where(Source.config_key == cfg.config_key))
    if source is not None and not reactivate and source.status == "unsubscribed":
        return source
    if source is not None:
        source.status = "active"
        source.unsubscribed_at = None
        source.config = cfg.config  # picks up an edited rule on re-seed
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
                   now: datetime, reactivate: bool = True) -> list[tuple[str, Source | None]]:
    """Each entry is a URL, or `{url, adapter, ...}`: the other keys are options for that
    adapter, except `ticket` (a Sparrow task id, for people). The `pending` list is not seeded."""
    entries = (yaml.safe_load(Path(path).read_text()) or {}).get("sources") or []
    out: list[tuple[str, Source | None]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            entry = {"url": entry}
        options = {k: v for k, v in entry.items() if k not in ("url", "adapter", "ticket")}
        url = entry["url"]
        try:
            out.append((url, subscribe(session, url, client, adapters, now, entry.get("adapter"), options,
                                          reactivate)))
        except Exception:
            session.rollback()
            log.exception("seed failed for %s", url)
            out.append((url, None))
    return out
