from datetime import date, datetime

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from newsletter import worker
from newsletter.clock import ET
from newsletter.config import get_settings
from newsletter.db import make_engine, sessionmaker_for
from newsletter.models import Base, Issue


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/w.sqlite"
    Base.metadata.create_all(make_engine(url))
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def test_scheduler_jobs_and_times():
    sched = worker.build_scheduler(BackgroundScheduler())
    jobs = {j.id: j for j in sched.get_jobs()}
    assert set(jobs) == {"ingest", "build", "email", "backup"}
    t = datetime(2026, 10, 27, 6, 0, tzinfo=ET)
    nxt = lambda job: job.trigger.get_next_fire_time(None, t)  # noqa: E731
    assert nxt(jobs["build"]) == datetime(2026, 10, 27, 6, 30, tzinfo=ET)
    assert nxt(jobs["email"]) == datetime(2026, 10, 27, 8, 0, tzinfo=ET)
    assert nxt(jobs["ingest"]) == datetime(2026, 10, 27, 6, 0, tzinfo=ET)
    assert nxt(jobs["backup"]) == datetime(2026, 10, 28, 3, 15, tzinfo=ET)


def test_scheduler_handles_dst_end():
    sched = worker.build_scheduler(BackgroundScheduler())
    build = sched.get_job("build")
    nxt = build.trigger.get_next_fire_time(None, datetime(2026, 11, 1, 0, 0, tzinfo=ET))
    assert nxt.utcoffset().total_seconds() == -5 * 3600 and (nxt.hour, nxt.minute) == (6, 30)


def test_job_swallows_errors(temp_db, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("ingest exploded")
    monkeypatch.setattr(worker, "ingest_all", boom)
    worker.ingest_job()  # returns normally


def test_catch_up_builds_missed_issue(temp_db):
    worker.catch_up(datetime(2026, 10, 27, 6, 45, tzinfo=ET))
    with sessionmaker_for(temp_db)() as s:
        assert s.scalar(select(Issue.issue_date)) == date(2026, 10, 27)


def test_catch_up_waits_before_build_time(temp_db):
    worker.catch_up(datetime(2026, 10, 27, 6, 0, tzinfo=ET))
    with sessionmaker_for(temp_db)() as s:
        assert s.scalar(select(Issue)) is None
