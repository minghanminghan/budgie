from datetime import date

from sqlalchemy import select

from newsletter.auth import verify_password
from newsletter.cli import main
from newsletter.db import sessionmaker_for
from newsletter.models import Issue
from tests.test_worker import temp_db  # noqa: F401  (fixture)


def test_cli_build_date(temp_db, capsys):  # noqa: F811
    assert main(["build", "--date", "2026-10-27"]) == 0
    with sessionmaker_for(temp_db)() as s:
        issue = s.scalars(select(Issue)).one()
    assert issue.issue_date == date(2026, 10, 27) and issue.status == "ok"
    assert "2026-10-27" in capsys.readouterr().out


def test_cli_hash_password(monkeypatch, capsys):
    monkeypatch.setattr("newsletter.cli.getpass", lambda prompt="": "s3cret")
    assert main(["hash-password"]) == 0
    assert verify_password("s3cret", capsys.readouterr().out.strip())
