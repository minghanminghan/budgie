import sqlite3
from datetime import date, timedelta

from newsletter.backup import backup_db
from newsletter.db import make_engine
from newsletter.models import Base


def test_backup_keeps_7(tmp_path):
    db = tmp_path / "live.sqlite"
    Base.metadata.create_all(make_engine(f"sqlite:///{db}"))
    out = tmp_path / "backups"
    start = date(2026, 10, 1)
    for i in range(9):
        path = backup_db(db, out, start + timedelta(days=i))
    files = sorted(p.name for p in out.iterdir())
    assert len(files) == 7
    assert files[-1] == "newsletter-2026-10-09.sqlite" == path.name
    assert files[0] == "newsletter-2026-10-03.sqlite"
    tables = {r[0] for r in sqlite3.connect(path).execute("select name from sqlite_master where type='table'")}
    assert "items" in tables


def test_backup_same_day_overwrites(tmp_path):
    db = tmp_path / "live.sqlite"
    Base.metadata.create_all(make_engine(f"sqlite:///{db}"))
    out = tmp_path / "backups"
    backup_db(db, out, date(2026, 10, 1))
    backup_db(db, out, date(2026, 10, 1))
    assert len(list(out.iterdir())) == 1
