import logging
import random
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from newsletter.backup import backup_db
from newsletter.clock import ET, issue_date_for
from newsletter.config import get_settings
from newsletter.db import sessionmaker_for, sqlite_path
from newsletter.embed import Embedder, embed_pending
from newsletter.emailer import ResendSender, send_daily_email
from newsletter.http import PoliteClient
from newsletter.ingest.base import default_adapters
from newsletter.ingest.pipeline import ingest_all
from newsletter.models import Issue
from newsletter.rank.issue import build_issue
from newsletter.rank.rules import RulesRanker

log = logging.getLogger("newsletter.worker")
BUILD_HOUR, BUILD_MINUTE = 6, 30


def _now() -> datetime:
    return datetime.now(UTC)


def _session():
    return sessionmaker_for(get_settings().database_url)()


@lru_cache
def _embedder(model_name: str) -> Embedder:
    return Embedder(model_name)  # the model loads once, on first use


def ingest_job() -> None:
    client = PoliteClient()
    try:
        adapters = {a.name: a for a in default_adapters()}
        with _session() as session:
            results = ingest_all(session, client, adapters, _now())
            log.info("ingest: %s", results)
            log.info("embedded %d items", embed_pending(session, _embedder(get_settings().embed_model)))
    except Exception:
        log.exception("ingest job failed")
    finally:
        client.close()


def _build(now: datetime) -> None:
    with _session() as session:
        issue = build_issue(session, issue_date_for(now), RulesRanker(), random.Random(), now)
        log.info("issue %s: %s %s", issue.issue_date, issue.status, issue.error or "")


def build_job() -> None:
    try:
        _build(_now())
    except Exception:
        log.exception("build job failed")


def email_job() -> None:
    try:
        settings = get_settings()
        with _session() as session:
            subject = send_daily_email(session, issue_date_for(_now()),
                                       ResendSender(settings.resend_api_key, settings.email_from), settings)
        log.info("email sent: %s", subject)
    except Exception:
        log.exception("email job failed")


def backup_job() -> None:
    try:
        settings = get_settings()
        path = backup_db(Path(sqlite_path(settings.database_url)), Path(settings.backup_dir),
                         issue_date_for(_now()))
        log.info("backup written: %s", path)
    except Exception:
        log.exception("backup job failed")


def catch_up(now: datetime) -> None:
    """After a restart past 06:30 ET, build today's issue if it is missing or failed."""
    try:
        local = now.astimezone(ET)
        if (local.hour, local.minute) < (BUILD_HOUR, BUILD_MINUTE):
            return
        with _session() as session:
            ok = session.scalar(select(Issue.id).where(Issue.issue_date == local.date(), Issue.status == "ok"))
        if ok is None:
            _build(now)
    except Exception:
        log.exception("catch-up build failed")


def build_scheduler(scheduler: BaseScheduler | None = None) -> BaseScheduler:
    scheduler = scheduler or BlockingScheduler(timezone=ET)
    opts = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600}
    scheduler.add_job(ingest_job, CronTrigger(minute=0, timezone=ET), id="ingest", **opts)
    scheduler.add_job(build_job, CronTrigger(hour=BUILD_HOUR, minute=BUILD_MINUTE, timezone=ET), id="build", **opts)
    scheduler.add_job(email_job, CronTrigger(hour=7, minute=0, timezone=ET), id="email", **opts)
    scheduler.add_job(backup_job, CronTrigger(hour=3, minute=15, timezone=ET), id="backup", **opts)
    return scheduler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    scheduler = build_scheduler()
    catch_up(_now())
    log.info("worker started")
    scheduler.start()


if __name__ == "__main__":
    main()
