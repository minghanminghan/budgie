from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy.orm import Session

from newsletter.clock import go_live_at
from newsletter.models import Event, Impression, Issue, Item
from newsletter.rank.issue import latest_thumbs

SCANNER_UA_MARKERS = ("bot", "crawler", "spider", "preview", "safelinks", "proofpoint", "mimecast", "barracuda")
CLICK_GRACE = timedelta(seconds=30)
THUMB_VALUES = ("up", "down", "clear")


def record_thumb(session: Session, impression_id: int, value: Literal["up", "down", "clear"],
                 user_agent: str | None, now: datetime) -> str | None:
    if value not in THUMB_VALUES:
        raise ValueError(f"bad thumb value: {value!r}")
    imp = session.get(Impression, impression_id)
    if imp is None:
        raise LookupError(f"unknown impression {impression_id}")
    session.add(Event(type=f"thumb_{value}", impression_id=imp.id, item_id=imp.item_id,
                      issue_id=imp.issue_id, created_at=now, user_agent=user_agent))
    session.commit()
    return current_thumbs(session, [imp.item_id]).get(imp.item_id)


def current_thumbs(session: Session, item_ids: list[int]) -> dict[int, str]:
    return latest_thumbs(session, item_ids) if item_ids else {}


def is_scanner(user_agent: str | None) -> bool:
    ua = (user_agent or "").lower()
    return not ua or any(m in ua for m in SCANNER_UA_MARKERS)


def record_click(session: Session, impression_id: int, user_agent: str | None, now: datetime,
                 owner: bool) -> Item | None:
    """Return the clicked item. Log the click only for the owner, a real
    browser, and not within CLICK_GRACE of the issue going live."""
    imp = session.get(Impression, impression_id)
    if imp is None:
        return None
    item = session.get(Item, imp.item_id)
    issue = session.get(Issue, imp.issue_id)
    if owner and not is_scanner(user_agent) and now >= go_live_at(issue.issue_date) + CLICK_GRACE:
        session.add(Event(type="click", impression_id=imp.id, item_id=imp.item_id,
                          issue_id=imp.issue_id, created_at=now, user_agent=user_agent))
        session.commit()
    return item


def record_page_view(session: Session, issue_id: int, user_agent: str | None, now: datetime) -> None:
    session.add(Event(type="page_view", issue_id=issue_id, created_at=now, user_agent=user_agent))
    session.commit()
