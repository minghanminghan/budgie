from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from newsletter.clock import go_live_at
from newsletter.feedback import current_thumbs, record_click, record_page_view, record_thumb
from newsletter.models import Event, Impression, Issue

DAY = date(2026, 10, 27)
GO_LIVE = go_live_at(DAY).astimezone(UTC)
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"


@pytest.fixture
def imp(session, make_source, make_item, now):
    item = make_item(make_source())
    issue = Issue(issue_date=DAY, built_at=now, status="ok", ranker_version="rules-v1")
    session.add(issue); session.flush()
    imp = Impression(issue_id=issue.id, item_id=item.id, slot=1, score=1.0, features={})
    session.add(imp); session.commit()
    return imp


def events(session, kind=None):
    q = select(func.count()).select_from(Event)
    if kind:
        q = q.where(Event.type == kind)
    return session.scalar(q)


def test_thumb_latest_wins(session, imp, now):
    assert record_thumb(session, imp.id, "up", UA, now) == "up"
    assert record_thumb(session, imp.id, "down", UA, now) == "down"
    assert current_thumbs(session, [imp.item_id]) == {imp.item_id: "down"}
    assert record_thumb(session, imp.id, "clear", UA, now) is None
    assert current_thumbs(session, [imp.item_id]) == {}
    ev = session.scalars(select(Event).order_by(Event.id)).first()
    assert (ev.type, ev.impression_id, ev.item_id, ev.issue_id) == ("thumb_up", imp.id, imp.item_id, imp.issue_id)


def test_thumb_unknown_impression_raises(session, now):
    with pytest.raises(LookupError):
        record_thumb(session, 999, "up", UA, now)


def test_thumb_bad_value_raises(session, imp, now):
    with pytest.raises(ValueError):
        record_thumb(session, imp.id, "sideways", UA, now)


def test_click_logged_only_for_owner(session, imp):
    item = record_click(session, imp.id, UA, GO_LIVE + timedelta(hours=1), owner=False)
    assert item.id == imp.item_id
    assert events(session) == 0
    record_click(session, imp.id, UA, GO_LIVE + timedelta(hours=1), owner=True)
    assert events(session, "click") == 1


def test_click_scanner_ua_ignored(session, imp):
    record_click(session, imp.id, "Mozilla/5.0 (compatible; Proofpoint)", GO_LIVE + timedelta(hours=1), owner=True)
    assert events(session) == 0


def test_click_within_grace_ignored(session, imp):
    record_click(session, imp.id, UA, GO_LIVE + timedelta(seconds=10), owner=True)
    assert events(session) == 0
    record_click(session, imp.id, UA, GO_LIVE + timedelta(seconds=31), owner=True)
    assert events(session) == 1


def test_click_unknown_impression(session):
    assert record_click(session, 999, UA, GO_LIVE, owner=True) is None


def test_page_view(session, imp, now):
    record_page_view(session, imp.issue_id, UA, now)
    ev = session.scalars(select(Event)).one()
    assert (ev.type, ev.issue_id, ev.user_agent) == ("page_view", imp.issue_id, UA)
