import re
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from newsletter.auth import SESSION_COOKIE, hash_password, make_session_token, sign_link
from newsletter.clock import ET
from newsletter.config import Settings
from newsletter.models import Event, Impression, Issue, Item, Source
from newsletter.web.app import create_app
from tests.fakes import FakeAdapter

SETTINGS = Settings(session_secret="sess-secret", link_signing_secret="link-secret", api_token="api-tok",
                    app_password_hash=hash_password("correct horse"), base_url="https://nl.example.ts.net")
AFTER_LIVE = datetime(2026, 10, 27, 8, 0, tzinfo=ET).astimezone(UTC)
BEFORE_LIVE = datetime(2026, 10, 27, 6, 45, tzinfo=ET).astimezone(UTC)
FEED = "https://fake.example.com/"
OWNER = {SESSION_COOKIE: make_session_token("sess-secret")}
BEARER = {"Authorization": "Bearer api-tok"}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def world(engine, session, make_source, make_item, now):
    src = make_source(title="Fake Blog")
    imps = {}
    for d, label in ((date(2026, 10, 26), "Monday"), (date(2026, 10, 27), "Tuesday")):
        issue = Issue(issue_date=d, built_at=now, status="ok", ranker_version="rules-v1")
        session.add(issue); session.flush()
        for slot in (1, 2):
            item = make_item(src, title=f"{label} post {slot}", author="Jane Doe", word_count=1000)
            imp = Impression(issue_id=issue.id, item_id=item.id, slot=slot, score=1.0,
                             features={"source_id": src.id})
            session.add(imp); session.flush()
            imps[(label, slot)] = imp
    session.commit()
    return {"source": src, "imps": imps}


@pytest.fixture
def clock():
    return Clock(AFTER_LIVE)


@pytest.fixture
def app(engine, clock):
    return create_app(settings=SETTINGS, session_factory=sessionmaker(engine, expire_on_commit=False),
                      client=None, adapters=[FakeAdapter(known=[FEED])], now=clock)


@pytest.fixture
def web(app):
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def owner(app):
    return TestClient(app, base_url="https://testserver", cookies=OWNER)


def n_events(session, kind):
    return session.scalar(select(func.count()).select_from(Event).where(Event.type == kind))


def test_index_shows_visible_issue_titles(web, world):
    r = web.get("/")
    assert r.status_code == 200
    assert "Tuesday post 1" in r.text and "Tuesday post 2" in r.text
    assert "Monday post 1" not in r.text
    assert "Fake Blog" in r.text and "Jane Doe" in r.text
    assert "5 min" in r.text  # ceil(1000 / 230)
    assert "subscribers only" not in r.text


def test_index_marks_subscriber_only_posts(web, world, session):
    item = session.get(Item, world["imps"][("Tuesday", 1)].item_id)
    item.is_teaser = True
    session.commit()
    assert web.get("/").text.count("subscribers only") == 1


def test_index_before_go_live_shows_previous_issue(web, world, clock):
    clock.t = BEFORE_LIVE
    r = web.get("/")
    assert "Monday post 1" in r.text
    assert "Tuesday post 1" not in r.text


def test_index_no_issue_yet(web):
    r = web.get("/")
    assert r.status_code == 200
    assert "No issue yet" in r.text


def test_thumb_buttons_disabled_when_logged_out(web, world):
    buttons = re.findall(r"<button[^>]*class=\"thumb[^>]*>", web.get("/").text)
    assert len(buttons) == 4
    assert all("disabled" in b for b in buttons)


def test_thumb_buttons_enabled_when_logged_in(owner, world, session):
    from newsletter.feedback import record_thumb
    imp = world["imps"][("Tuesday", 1)]
    record_thumb(session, imp.id, "up", "ua", AFTER_LIVE)
    buttons = re.findall(r"<button[^>]*class=\"thumb[^>]*>", owner.get("/").text)
    assert len(buttons) == 4
    assert not any("disabled" in b for b in buttons)
    pressed = [b for b in buttons if 'aria-pressed="true"' in b]
    assert len(pressed) == 1 and f'data-impression="{imp.id}"' in pressed[0] and 'data-value="up"' in pressed[0]


def test_feedback_requires_owner(web, world):
    imp = world["imps"][("Tuesday", 1)]
    assert web.post("/api/feedback", json={"impression_id": imp.id, "value": "up"}).status_code == 401


def test_feedback_requires_json(owner, world):
    imp = world["imps"][("Tuesday", 1)]
    r = owner.post("/api/feedback", data={"impression_id": imp.id, "value": "up"})
    assert r.status_code == 415


def test_feedback_ok(owner, world, session):
    imp = world["imps"][("Tuesday", 1)]
    r = owner.post("/api/feedback", json={"impression_id": imp.id, "value": "up"})
    assert r.status_code == 200
    assert r.json() == {"value": "up"}
    assert n_events(session, "thumb_up") == 1


def test_feedback_bad_input(owner, world):
    imp = world["imps"][("Tuesday", 1)]
    assert owner.post("/api/feedback", json={"impression_id": 9999, "value": "up"}).status_code == 404
    assert owner.post("/api/feedback", json={"impression_id": imp.id, "value": "meh"}).status_code == 422


def test_bearer_token_can_subscribe(web, session):
    r = web.post("/api/sources", json={"url": FEED}, headers=BEARER)
    assert r.status_code == 201
    body = r.json()
    assert body["message"] == "Subscribed to Fake Blog"
    assert session.get(Source, body["id"]).status == "active"


def test_preview(owner):
    r = owner.post("/api/sources/preview", json={"url": FEED})
    assert r.json() == {"found": True, "adapter": "fake", "title": "Fake Blog",
                        "site_domain": "fake.example.com", "preview_titles": ["One", "Two", "Three"]}
    r = owner.post("/api/sources/preview", json={"url": "https://nope.example.com/"})
    assert r.json() == {"found": False, "message": "No feed found"}


def test_subscribe_no_feed_422(web):
    r = web.post("/api/sources", json={"url": "https://nope.example.com/"}, headers=BEARER)
    assert r.status_code == 422
    assert r.json() == {"message": "No feed found"}


def test_unsubscribe(owner, world):
    src = world["source"]
    assert "Fake Blog" in owner.get("/sources").text
    r = owner.request("DELETE", f"/api/sources/{src.id}", json={})
    assert r.status_code == 200
    assert "Fake Blog" not in owner.get("/sources").text
    assert owner.request("DELETE", "/api/sources/9999", json={}).status_code == 404


def test_click_redirects_and_logs_only_owner(web, owner, world, session):
    imp = world["imps"][("Tuesday", 1)]
    token = sign_link(imp.id, "link-secret")
    r = web.get(f"/c/{token}", follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 Safari"})
    assert r.status_code == 302
    assert r.headers["location"].startswith("https://")
    assert n_events(session, "click") == 0
    r = owner.get(f"/c/{token}", follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 Safari"})
    assert r.status_code == 302
    assert n_events(session, "click") == 1


def test_click_bad_token_404(web, world):
    assert web.get("/c/not-a-token", follow_redirects=False).status_code == 404


def test_post_links_use_signed_click_urls(web, world):
    imp = world["imps"][("Tuesday", 1)]
    assert f'href="/c/{sign_link(imp.id, "link-secret")}"' in web.get("/").text


def test_login_flow(web):
    assert web.get("/login").status_code == 200
    r = web.post("/login", data={"password": "wrong"}, follow_redirects=False)
    assert r.status_code == 401
    r = web.post("/login", data={"password": "correct horse"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie and "Secure" in cookie and "samesite=lax" in cookie.lower()
    for _ in range(9):
        web.post("/login", data={"password": "wrong"})
    assert web.post("/login", data={"password": "wrong"}).status_code == 429
    assert web.post("/login", data={"password": "correct horse"}).status_code == 429


def test_logout_clears_cookie(owner):
    r = owner.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert f'{SESSION_COOKIE}=""' in r.headers["set-cookie"] or "Max-Age=0" in r.headers["set-cookie"]


def test_issue_by_date_and_archive(web, world):
    r = web.get("/issue/2026-10-26")
    assert r.status_code == 200 and "Monday post 1" in r.text
    assert web.get("/issue/2020-01-01").status_code == 404
    assert web.get("/issue/not-a-date").status_code == 404
    text = web.get("/archive").text
    assert text.index("2026-10-27") < text.index("2026-10-26")


def test_issue_not_live_yet_404(web, world, clock):
    clock.t = BEFORE_LIVE
    assert web.get("/issue/2026-10-27").status_code == 404
    assert "2026-10-27" not in web.get("/archive").text


def test_page_view_logged_only_for_owner(web, owner, world, session):
    web.get("/")
    assert n_events(session, "page_view") == 0
    owner.get("/")
    assert n_events(session, "page_view") == 1


def test_sources_form_only_for_owner(web, owner, world, session):
    world["source"].last_error = "ReadTimeout: boom"
    session.commit()
    anon = web.get("/sources").text
    assert "Fake Blog" in anon
    assert "<form" not in anon and "Unsubscribe" not in anon and "ReadTimeout" not in anon
    mine = owner.get("/sources").text
    assert "<form" in mine and "Unsubscribe" in mine and "ReadTimeout" in mine


def test_healthz(web):
    assert web.get("/healthz").json() == {"ok": True}


def test_version_is_public(web):
    from importlib.metadata import version
    # no login: hawk deploy checks it after a restart
    assert web.get("/api/version").json() == {"version": version("newsletter")}


def test_missing_secrets_refuse_to_start(engine):
    with pytest.raises(ValueError):
        create_app(settings=Settings(), session_factory=sessionmaker(engine))
