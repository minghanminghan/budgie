from datetime import UTC, datetime, timedelta

from itsdangerous import TimestampSigner

from newsletter.auth import (
    LoginLimiter, check_session_token, hash_password, is_owner, make_session_token,
    sign_link, unsign_link, verify_password,
)
from newsletter.config import Settings

SECRET = "s3cret-session"
T0 = datetime(2026, 10, 27, 12, tzinfo=UTC)


def test_password_roundtrip():
    h = hash_password("hunter2")
    assert h.startswith("scrypt$")
    assert verify_password("hunter2", h)
    assert not verify_password("hunter3", h)
    assert hash_password("hunter2") != h
    assert not verify_password("hunter2", "garbage")
    assert not verify_password("hunter2", "")


def test_session_token_valid_and_expired(monkeypatch):
    assert check_session_token(make_session_token(SECRET), SECRET)
    assert not check_session_token(make_session_token(SECRET), "other-secret")
    assert not check_session_token(None, SECRET)
    assert not check_session_token("junk", SECRET)
    old = int(T0.timestamp()) - 181 * 24 * 3600
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: old)
    stale = make_session_token(SECRET)
    monkeypatch.undo()
    assert not check_session_token(stale, SECRET)


def test_empty_secret_never_authenticates():
    assert not check_session_token(make_session_token(""), "")


def test_link_token_tamper_rejected():
    s = "link-secret"
    assert unsign_link(sign_link(7, s), s) == 7
    assert unsign_link(sign_link(7, s)[:-1] + "x", s) is None
    assert unsign_link("7", s) is None
    assert unsign_link(sign_link(7, "other"), s) is None


def test_login_limiter_blocks_after_10():
    lim = LoginLimiter()
    for _ in range(9):
        lim.record_failure(T0)
    assert lim.allowed(T0)
    lim.record_failure(T0)
    assert not lim.allowed(T0)
    assert lim.allowed(T0 + timedelta(minutes=61))


def test_is_owner_cookie_or_bearer():
    settings = Settings(session_secret=SECRET, api_token="tok-123")
    cookie = make_session_token(SECRET)
    assert is_owner(cookie, None, settings)
    assert is_owner(None, "Bearer tok-123", settings)
    assert not is_owner(None, "Bearer nope", settings)
    assert not is_owner(None, None, settings)
    assert not is_owner(None, "tok-123", settings)


def test_empty_api_token_never_matches():
    assert not is_owner(None, "Bearer ", Settings(session_secret=SECRET, api_token=""))
