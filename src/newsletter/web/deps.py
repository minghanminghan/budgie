from collections.abc import Iterator
from datetime import datetime

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from newsletter.auth import SESSION_COOKIE, is_owner


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def now_of(request: Request) -> datetime:
    return request.app.state.now()


def owner_of(request: Request) -> bool:
    return is_owner(request.cookies.get(SESSION_COOKIE), request.headers.get("authorization"),
                    request.app.state.settings)


def require_owner_json(request: Request) -> None:
    """Guard for every /api write: owner (401) and a JSON body (415, the CSRF guard)."""
    if not owner_of(request):
        raise HTTPException(401, "Owner only")
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Content-Type must be application/json")
