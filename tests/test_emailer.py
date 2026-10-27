from datetime import date

import pytest

from newsletter.config import Settings
from newsletter.emailer import ResendSender, compose_daily_email, send_daily_email
from newsletter.models import Impression, Issue

BASE = "https://newsletter.tail1234.ts.net"
DAY = date(2026, 10, 27)


class FakeSender:
    def __init__(self):
        self.calls = []

    def send(self, to, subject, text):
        self.calls.append((to, subject, text))


@pytest.mark.parametrize("n,subject", [
    (18, "Today's 18 posts are ready"),
    (1, "Today's 1 post is ready"),
    (0, "No new posts today"),
])
def test_ok_subjects(n, subject, now):
    issue = Issue(issue_date=DAY, built_at=now, status="ok", ranker_version="rules-v1")
    got_subject, body = compose_daily_email(issue, n, BASE)
    assert got_subject == subject
    assert body == f"{subject}: {BASE}/"


def test_trailing_slash_base_url(now):
    issue = Issue(issue_date=DAY, built_at=now, status="ok")
    assert compose_daily_email(issue, 2, BASE + "/")[1].endswith(f"{BASE}/")


def test_failed_issue_email(now):
    issue = Issue(issue_date=DAY, built_at=now, status="failed", error="RuntimeError: ranker exploded")
    subject, body = compose_daily_email(issue, 0, BASE)
    assert subject == "Today's issue failed"
    assert "RuntimeError: ranker exploded" in body


def test_missing_issue_email():
    subject, body = compose_daily_email(None, 0, BASE)
    assert subject == "Today's issue failed"
    assert "no issue was built" in body


def test_send_daily_email_uses_today_issue(session, make_source, make_item, now):
    src = make_source()
    issue = Issue(issue_date=DAY, built_at=now, status="ok", ranker_version="rules-v1")
    other = Issue(issue_date=date(2026, 10, 26), built_at=now, status="ok", ranker_version="rules-v1")
    session.add_all([issue, other]); session.flush()
    for slot in (1, 2):
        session.add(Impression(issue_id=issue.id, item_id=make_item(src).id, slot=slot, score=1.0, features={}))
    session.add(Impression(issue_id=other.id, item_id=make_item(src).id, slot=1, score=1.0, features={}))
    session.commit()
    sender = FakeSender()
    settings = Settings(email_to="me@example.com", base_url=BASE)
    assert send_daily_email(session, DAY, sender, settings) == "Today's 2 posts are ready"
    assert sender.calls == [("me@example.com", "Today's 2 posts are ready", f"Today's 2 posts are ready: {BASE}/")]


def test_resend_sender_payload(monkeypatch):
    import resend
    sent = []
    monkeypatch.setattr(resend.Emails, "send", lambda params: sent.append(params) or {"id": "x"})
    ResendSender("re_key", "News <news@example.com>").send("me@example.com", "S", "T")
    assert sent == [{"from": "News <news@example.com>", "to": ["me@example.com"], "subject": "S", "text": "T"}]
    assert resend.api_key == "re_key"
