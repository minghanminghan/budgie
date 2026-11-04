from datetime import date
from html import escape

import pytest

from newsletter.auth import unsign_link
from newsletter.config import Settings
from newsletter.emailer import ResendSender, compose_daily_email, send_daily_email
from newsletter.models import Impression, Issue

BASE = "https://newsletter.tail1234.ts.net"
DAY = date(2026, 10, 27)


class FakeSender:
    def __init__(self):
        self.calls = []

    def send(self, to, subject, text, html):
        self.calls.append((to, subject, text, html))


def post(i, **kw):
    return {"impression_id": i, "href": f"/c/tok{i}", "title": f"Post {i}", "source": "Blog",
            "author": None, "minutes": 3, "thumb": None, "is_teaser": False, **kw}


@pytest.mark.parametrize("n,subject", [
    (18, "Today's 18 posts are ready"),
    (1, "Today's 1 post is ready"),
    (0, "No new posts today"),
])
def test_ok_subjects(n, subject, now):
    issue = Issue(issue_date=DAY, built_at=now, status="ok", ranker_version="rules-v1")
    got_subject, body, html = compose_daily_email(issue, [post(i) for i in range(n)], BASE)
    assert got_subject == subject
    assert body.splitlines()[0] == f"{subject}: {BASE}/"
    assert f'<a href="{BASE}/">{escape(subject)}</a>' in html


def test_posts_link_like_the_page(now):
    issue = Issue(issue_date=DAY, built_at=now, status="ok")
    posts = [post(1, author="Jane Doe"), post(2, title="A <b> & c", is_teaser=True)]
    _, body, html = compose_daily_email(issue, posts, BASE + "/")
    assert body == (f"Today's 2 posts are ready: {BASE}/\n\n"
                    f"Post 1\nBlog, Jane Doe, 3 min\n{BASE}/c/tok1\n\n"
                    f"A <b> & c\nBlog, 3 min, subscribers only\n{BASE}/c/tok2")
    assert f'<a href="{BASE}/c/tok1">Post 1</a>' in html
    assert f'<a href="{BASE}/c/tok2">A &lt;b&gt; &amp; c</a>' in html
    assert "Blog, 3 min, subscribers only" in html


def test_failed_issue_email(now):
    issue = Issue(issue_date=DAY, built_at=now, status="failed", error="RuntimeError: ranker exploded")
    subject, body, html = compose_daily_email(issue, [], BASE)
    assert subject == "Today's issue failed"
    assert body == f"Today's issue failed: RuntimeError: ranker exploded. {BASE}/"
    assert f'<a href="{BASE}/">' in html


def test_missing_issue_email():
    subject, body, _ = compose_daily_email(None, [], BASE)
    assert subject == "Today's issue failed"
    assert "no issue was built" in body


def test_send_daily_email_uses_today_issue(session, make_source, make_item, now):
    src = make_source()
    issue = Issue(issue_date=DAY, built_at=now, status="ok", ranker_version="rules-v1")
    other = Issue(issue_date=date(2026, 10, 26), built_at=now, status="ok", ranker_version="rules-v1")
    session.add_all([issue, other]); session.flush()
    for slot, title in ((1, "First"), (2, "Second")):
        item_id = make_item(src, title=title).id
        session.add(Impression(issue_id=issue.id, item_id=item_id, slot=slot, score=1.0, features={}))
    session.add(Impression(issue_id=other.id, item_id=make_item(src).id, slot=1, score=1.0, features={}))
    session.commit()
    sender = FakeSender()
    settings = Settings(email_to="me@example.com", base_url=BASE, link_signing_secret="s")
    assert send_daily_email(session, DAY, sender, settings) == "Today's 2 posts are ready"
    [(to, subject, text, _)] = sender.calls
    assert (to, subject) == ("me@example.com", "Today's 2 posts are ready")
    lines = text.splitlines()
    assert [lines[2], lines[6]] == ["First", "Second"]
    tokens = [line.removeprefix(f"{BASE}/c/") for line in (lines[4], lines[8])]
    slots = {i.id: i.slot for i in session.query(Impression)}
    assert [slots[unsign_link(t, "s")] for t in tokens] == [1, 2]


def test_resend_sender_payload(monkeypatch):
    import resend
    sent = []
    monkeypatch.setattr(resend.Emails, "send", lambda params: sent.append(params) or {"id": "x"})
    ResendSender("re_key", "News <news@example.com>").send("me@example.com", "S", "T", "<p>H</p>")
    assert sent == [{"from": "News <news@example.com>", "to": ["me@example.com"], "subject": "S", "text": "T",
                     "html": "<p>H</p>"}]
    assert resend.api_key == "re_key"
