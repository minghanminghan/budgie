import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from itsdangerous import BadSignature, Signer, TimestampSigner

from newsletter.config import Settings

SESSION_COOKIE = "nl_session"
SESSION_MAX_AGE = 180 * 24 * 3600
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
_SESSION_VALUE = b"owner"


def _scrypt(pw: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pw.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    return f"scrypt${salt.hex()}${_scrypt(pw, salt).hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        scheme, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        return hmac.compare_digest(_scrypt(pw, bytes.fromhex(salt_hex)), bytes.fromhex(hash_hex))
    except ValueError:
        return False


def make_session_token(secret: str) -> str:
    return TimestampSigner(secret, salt="nl-session").sign(_SESSION_VALUE).decode()


def check_session_token(token: str | None, secret: str) -> bool:
    if not token or not secret:
        return False
    try:
        value = TimestampSigner(secret, salt="nl-session").unsign(token, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return False
    return hmac.compare_digest(value, _SESSION_VALUE)


def sign_link(impression_id: int, secret: str) -> str:
    return Signer(secret, salt="nl-click").sign(str(impression_id)).decode()


def unsign_link(token: str, secret: str) -> int | None:
    if not secret:
        return None
    try:
        return int(Signer(secret, salt="nl-click").unsign(token))
    except (BadSignature, ValueError):
        return None


class LoginLimiter:
    """Guards the public password form: at most `max_failures` per `window`."""

    def __init__(self, max_failures: int = 10, window: timedelta = timedelta(hours=1)):
        self.max_failures = max_failures
        self.window = window
        self._failures: list[datetime] = []

    def _prune(self, now: datetime) -> None:
        self._failures = [t for t in self._failures if now - t < self.window]

    def allowed(self, now: datetime) -> bool:
        self._prune(now)
        return len(self._failures) < self.max_failures

    def record_failure(self, now: datetime) -> None:
        self._prune(now)
        self._failures.append(now)


def is_owner(cookie: str | None, authorization: str | None, settings: Settings) -> bool:
    if check_session_token(cookie, settings.session_secret):
        return True
    if not authorization or not settings.api_token:
        return False
    scheme, _, token = authorization.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(token.strip().encode(), settings.api_token.encode())
