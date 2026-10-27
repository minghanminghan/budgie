import math
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from newsletter.auth import (
    SESSION_COOKIE, SESSION_MAX_AGE, check_session_token, make_session_token, sign_link, unsign_link,
    verify_password,
)
from newsletter.clock import go_live_at, issue_date_for
from newsletter.feedback import current_thumbs, record_click, record_page_view
from newsletter.models import Impression, Issue, Item, ItemSource, Source
from newsletter.rank.issue import visible_issue
from newsletter.web.deps import get_db, now_of, owner_of

router = APIRouter()
WORDS_PER_MINUTE = 230


def render(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
    ctx.setdefault("owner", owner_of(request))
    return request.app.state.templates.TemplateResponse(request, name, ctx, status_code=status)


def _last_live_date(now) -> date:
    today = issue_date_for(now)
    return today if now >= go_live_at(today) else today - timedelta(days=1)


def _posts(session: Session, issue: Issue, link_secret: str) -> list[dict]:
    rows = session.execute(
        select(Impression, Item).join(Item, Item.id == Impression.item_id)
        .where(Impression.issue_id == issue.id).order_by(Impression.slot)
    ).all()
    if not rows:
        return []
    item_ids = [item.id for _, item in rows]
    titles = dict(session.execute(select(Source.id, Source.title)).all())
    first_source = {}
    for item_id, source_id in session.execute(
        select(ItemSource.item_id, ItemSource.source_id).where(ItemSource.item_id.in_(item_ids))
        .order_by(ItemSource.seen_at)
    ):
        first_source.setdefault(item_id, source_id)
    thumbs = current_thumbs(session, item_ids)
    return [
        {
            "impression_id": imp.id,
            "href": f"/c/{sign_link(imp.id, link_secret)}",
            "title": item.title,
            "source": titles.get(imp.features.get("source_id") or first_source.get(item.id), item.site_domain),
            "author": item.author,
            "minutes": max(1, math.ceil(item.word_count / WORDS_PER_MINUTE)),
            "thumb": thumbs.get(item.id),
        }
        for imp, item in rows
    ]


def _issue_page(request: Request, session: Session, issue: Issue | None) -> HTMLResponse:
    owner = owner_of(request)
    if issue is not None and owner:
        record_page_view(session, issue.id, request.headers.get("user-agent"), now_of(request))
    posts = _posts(session, issue, request.app.state.settings.link_signing_secret) if issue else []
    return render(request, "issue.html", owner=owner, issue=issue, posts=posts)


@router.get("/", response_class=HTMLResponse)
def index(request: Request, session: Session = Depends(get_db)):
    return _issue_page(request, session, visible_issue(session, now_of(request)))


@router.get("/issue/{day}", response_class=HTMLResponse)
def issue_by_date(day: str, request: Request, session: Session = Depends(get_db)):
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(404) from None
    issue = session.scalar(select(Issue).where(Issue.issue_date == d, Issue.status == "ok"))
    if issue is None or d > _last_live_date(now_of(request)):
        raise HTTPException(404)
    return _issue_page(request, session, issue)


@router.get("/archive", response_class=HTMLResponse)
def archive(request: Request, session: Session = Depends(get_db)):
    rows = session.execute(
        select(Issue.issue_date, func.count(Impression.id))
        .outerjoin(Impression, Impression.issue_id == Issue.id)
        .where(Issue.status == "ok", Issue.issue_date <= _last_live_date(now_of(request)))
        .group_by(Issue.id).order_by(Issue.issue_date.desc())
    ).all()
    return render(request, "archive.html", issues=rows)


@router.get("/sources", response_class=HTMLResponse)
def sources(request: Request, session: Session = Depends(get_db)):
    rows = session.scalars(select(Source).where(Source.status == "active").order_by(Source.title)).all()
    return render(request, "sources.html", sources=rows)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return render(request, "login.html", error=None)


@router.post("/login")
def login(request: Request, password: str = Form("")):
    now = now_of(request)
    limiter = request.app.state.limiter
    if not limiter.allowed(now):
        return render(request, "login.html", status=429, error="Too many attempts. Try again later.")
    settings = request.app.state.settings
    if not settings.app_password_hash or not verify_password(password, settings.app_password_hash):
        limiter.record_failure(now)
        return render(request, "login.html", status=401, error="Wrong password.")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, make_session_token(settings.session_secret), max_age=SESSION_MAX_AGE,
                    httponly=True, secure=True, samesite="lax")
    return resp


@router.post("/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(SESSION_COOKIE, httponly=True, secure=True, samesite="lax")
    return resp


@router.get("/c/{token}")
def click(token: str, request: Request, session: Session = Depends(get_db)):
    settings = request.app.state.settings
    impression_id = unsign_link(token, settings.link_signing_secret)
    if impression_id is None:
        raise HTTPException(404)
    # Clicks count only with the owner's session cookie (not a bearer token).
    owner = check_session_token(request.cookies.get(SESSION_COOKIE), settings.session_secret)
    item = record_click(session, impression_id, request.headers.get("user-agent"), now_of(request), owner)
    if item is None:
        raise HTTPException(404)
    return RedirectResponse(item.url, status_code=302)


@router.get("/healthz")
def healthz():
    return {"ok": True}
