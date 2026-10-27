import argparse
import logging
import random
import sys
from datetime import UTC, date, datetime
from getpass import getpass
from pathlib import Path

from newsletter.auth import hash_password
from newsletter.backup import backup_db
from newsletter.clock import issue_date_for
from newsletter.config import get_settings
from newsletter.db import sessionmaker_for, sqlite_path
from newsletter.embed import Embedder, embed_pending
from newsletter.emailer import ResendSender, send_daily_email
from newsletter.export import export_parquet
from newsletter.http import PoliteClient
from newsletter.ingest.base import default_adapters
from newsletter.ingest.pipeline import ingest_all
from newsletter.ingest.sources import seed_from_yaml
from newsletter.models import Source
from newsletter.rank.issue import build_issue
from newsletter.rank.rules import RulesRanker


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="newsletter")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", help="subscribe to every URL in a sources.yaml").add_argument("path", type=Path)
    sub.add_parser("ingest", help="fetch new posts from all active sources")
    sub.add_parser("embed", help="embed items that have no vector yet")
    b = sub.add_parser("build", help="build the issue for a date (default: today, ET)")
    b.add_argument("--date", type=date.fromisoformat)
    sub.add_parser("email", help="send today's email now")
    sub.add_parser("backup", help="write a SQLite backup now")
    sub.add_parser("export", help="write tables to Parquet").add_argument("out_dir", type=Path)
    sub.add_parser("hash-password", help="print a hash for APP_PASSWORD_HASH")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.cmd == "hash-password":
        pw = getpass("Password: ")
        if not pw:
            print("empty password", file=sys.stderr)
            return 1
        print(hash_password(pw))
        return 0

    settings = get_settings()
    now = datetime.now(UTC)
    Session = sessionmaker_for(settings.database_url)

    if args.cmd == "seed":
        client = PoliteClient()
        with Session() as session:
            results = seed_from_yaml(session, args.path, client, default_adapters(), now)
        for url, source in results:
            print(f"ok      {url} -> {source.adapter}: {source.title}" if source else f"FAILED  {url} (no feed found)")
        return 0 if all(s for _, s in results) else 1
    if args.cmd == "ingest":
        adapters = {a.name: a for a in default_adapters()}
        with Session() as session:
            results = ingest_all(session, PoliteClient(), adapters, now)
            titles = {s.id: s.title for s in session.query(Source)}
        for source_id, result in results.items():
            print(f"{'ok' if isinstance(result, int) else 'FAILED':7} {titles[source_id]}: {result}")
        return 0 if all(isinstance(r, int) for r in results.values()) else 1
    if args.cmd == "embed":
        with Session() as session:
            print(f"embedded {embed_pending(session, Embedder(settings.embed_model))} items")
        return 0
    if args.cmd == "build":
        with Session() as session:
            issue = build_issue(session, args.date or issue_date_for(now), RulesRanker(), random.Random(), now)
            print(f"issue {issue.issue_date}: {issue.status}{' ' + issue.error if issue.error else ''}")
        return 0 if issue.status == "ok" else 1
    if args.cmd == "email":
        with Session() as session:
            print(send_daily_email(session, issue_date_for(now),
                                   ResendSender(settings.resend_api_key, settings.email_from), settings))
        return 0
    if args.cmd == "backup":
        print(backup_db(Path(sqlite_path(settings.database_url)), Path(settings.backup_dir), issue_date_for(now)))
        return 0
    if args.cmd == "export":
        for path in export_parquet(Session.kw["bind"], args.out_dir):
            print(path)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
