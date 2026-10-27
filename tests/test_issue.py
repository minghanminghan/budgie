import random
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from newsletter.clock import ET
from newsletter.models import Event, Impression, Issue, ItemSource
from newsletter.rank.base import Candidate
from newsletter.rank.issue import build_issue, compose_issue, get_candidates, visible_issue
from newsletter.rank.rules import RulesRanker

DAY = date(2026, 10, 27)
FEATURE_KEYS = {"source_id", "author", "word_count", "is_teaser", "age_hours",
                "source_ups", "source_downs", "clicks"}


def cand(i, source_id=None, clicks=0):
    return Candidate(item_id=i, source_id=source_id if source_id is not None else i, author=None,
                     word_count=500, is_teaser=False, age_hours=1.0, source_ups=0,
                     source_downs=0, clicks=clicks)


class BoomRanker:
    version = "boom"

    def score(self, candidates):
        raise RuntimeError("ranker exploded")


def n_rows(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_candidates_exclude_shown_old_and_unsubscribed(session, make_source, make_item, now):
    src = make_source()
    gone = make_source(status="unsubscribed")
    fresh = make_item(src)
    no_date = make_item(src, published_at=None, first_seen_at=now - timedelta(days=1))
    make_item(src, published_at=now - timedelta(days=8))
    make_item(gone)
    shown = make_item(src)
    old_issue = Issue(issue_date=date(2026, 10, 20), built_at=now, status="ok", ranker_version="x")
    session.add(old_issue); session.flush()
    session.add(Impression(issue_id=old_issue.id, item_id=shown.id, slot=1, score=1.0, features={}))
    session.commit()
    ids = {c.item_id for c in get_candidates(session, now)}
    assert ids == {fresh.id, no_date.id}


def test_candidates_source_and_thumb_counts(session, make_source, make_item, now):
    a, b = make_source(), make_source()
    liked = make_item(a)
    session.add(Event(type="thumb_up", item_id=liked.id, created_at=now - timedelta(days=9)))
    disliked = make_item(a, published_at=now - timedelta(days=9))
    session.add(Event(type="thumb_up", item_id=disliked.id, created_at=now - timedelta(days=9)))
    session.add(Event(type="thumb_down", item_id=disliked.id, created_at=now - timedelta(days=8)))
    shared = make_item(b)  # linked to b first, then a
    session.add(ItemSource(item_id=shared.id, source_id=a.id, seen_at=now + timedelta(minutes=1), raw_url="x"))
    session.add(Event(type="click", item_id=shared.id, created_at=now))
    session.commit()
    by_id = {c.item_id: c for c in get_candidates(session, now)}
    assert by_id[liked.id].source_ups == 1 and by_id[liked.id].source_downs == 1
    assert by_id[shared.id].source_id == b.id
    assert by_id[shared.id].clicks == 1


def test_compose_small_pool_takes_all_no_wildcards():
    picks = compose_issue([cand(i) for i in range(5)], [5, 4, 3, 2, 1], random.Random(0))
    assert [p.slot for p in picks] == [1, 2, 3, 4, 5]
    assert [p.item_id for p in picks] == [0, 1, 2, 3, 4]
    assert not any(p.is_wildcard for p in picks)


def test_compose_orders_by_score_then_clicks():
    picks = compose_issue([cand(1), cand(2, clicks=3)], [1.0, 1.0], random.Random(0))
    assert [p.item_id for p in picks] == [2, 1]


def test_compose_per_source_cap():
    cands = [cand(i, source_id=1) for i in range(6)] + [cand(10, source_id=2), cand(11, source_id=2)]
    picks = compose_issue(cands, [float(10 - i) for i in range(8)], random.Random(0))
    assert len(picks) == 5
    assert sum(1 for p in picks if p.features["source_id"] == 1) == 3


def test_compose_large_pool_has_wildcards():
    cands = [cand(i, source_id=i // 2) for i in range(40)]
    scores = [float(40 - i) for i in range(40)]
    picks = compose_issue(cands, scores, random.Random(1))
    assert len(picks) == 20
    wild = [p for p in picks if p.is_wildcard]
    k = len(wild)
    assert k in (2, 3)
    top = set(range(20 - k))
    assert all(p.item_id not in top for p in wild)
    assert {p.item_id for p in picks if not p.is_wildcard} == top
    assert sorted(p.slot for p in picks) == list(range(1, 21))


def test_build_issue_writes_impressions(session, make_source, make_item, now):
    src = make_source()
    for _ in range(3):
        make_item(src)
    session.commit()
    issue = build_issue(session, DAY, RulesRanker(), random.Random(0), now)
    assert issue.status == "ok" and issue.ranker_version == "rules-v1"
    imps = session.scalars(select(Impression).where(Impression.issue_id == issue.id)).all()
    assert len(imps) == 3
    assert all(set(i.features) == FEATURE_KEYS for i in imps)
    assert sorted(i.slot for i in imps) == [1, 2, 3]


def test_build_issue_idempotent(session, make_source, make_item, now):
    src = make_source()
    for _ in range(3):
        make_item(src)
    session.commit()
    first = build_issue(session, DAY, RulesRanker(), random.Random(0), now)
    second = build_issue(session, DAY, RulesRanker(), random.Random(0), now)
    assert first.id == second.id
    assert n_rows(session, Issue) == 1
    assert n_rows(session, Impression) == 3


def test_build_issue_failure_recorded(session, make_source, make_item, now):
    make_item(make_source())
    session.commit()
    issue = build_issue(session, DAY, BoomRanker(), random.Random(0), now)
    assert issue.status == "failed"
    assert "ranker exploded" in issue.error
    assert n_rows(session, Impression) == 0


def test_build_issue_retries_failed(session, make_source, make_item, now):
    make_item(make_source())
    session.commit()
    build_issue(session, DAY, BoomRanker(), random.Random(0), now)
    build_issue(session, DAY, BoomRanker(), random.Random(0), now)
    assert n_rows(session, Issue) == 1
    issue = build_issue(session, DAY, RulesRanker(), random.Random(0), now)
    assert issue.status == "ok"
    assert n_rows(session, Issue) == 1
    assert n_rows(session, Impression) == 1


def test_build_issue_empty_pool_ok(session, now):
    issue = build_issue(session, DAY, RulesRanker(), random.Random(0), now)
    assert issue.status == "ok"
    assert n_rows(session, Impression) == 0


def test_visible_issue_before_and_after_go_live(session, now):
    for d in (date(2026, 10, 26), date(2026, 10, 27)):
        session.add(Issue(issue_date=d, built_at=now, status="ok", ranker_version="x"))
    session.commit()
    before = datetime(2026, 10, 27, 6, 45, tzinfo=ET).astimezone(UTC)
    at = datetime(2026, 10, 27, 7, 0, tzinfo=ET).astimezone(UTC)
    assert visible_issue(session, before).issue_date == date(2026, 10, 26)
    assert visible_issue(session, at).issue_date == date(2026, 10, 27)


def test_visible_issue_skips_failed(session, now):
    session.add(Issue(issue_date=date(2026, 10, 26), built_at=now, status="ok", ranker_version="x"))
    session.add(Issue(issue_date=date(2026, 10, 27), built_at=now, status="failed", error="e"))
    session.commit()
    assert visible_issue(session, now + timedelta(hours=5)).issue_date == date(2026, 10, 26)
