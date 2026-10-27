from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from newsletter.db import make_engine
from newsletter.models import Source

ROOT = Path(__file__).resolve().parents[1]


def test_datetime_roundtrip_is_aware_utc(session, make_source):
    src = make_source()
    session.commit(); session.expire_all()
    assert session.get(Source, src.id).created_at.tzinfo is not None


def test_canonical_url_unique(session, make_source, make_item):
    s = make_source()
    make_item(s, canonical_url="https://a.com/x")
    with pytest.raises(IntegrityError):
        make_item(s, canonical_url="https://a.com/x"); session.flush()


def test_alembic_upgrade_creates_all_tables(tmp_path):
    url = f"sqlite:///{tmp_path}/t.sqlite"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = make_engine(url)
    assert set(inspect(engine).get_table_names()) >= {
        "sources", "items", "item_sources", "embeddings", "issues",
        "impressions", "events", "ranker_models"}


def test_file_db_uses_wal(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/w.sqlite")
    assert engine.connect().exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
