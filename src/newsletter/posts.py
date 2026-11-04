import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from newsletter.auth import sign_link
from newsletter.feedback import current_thumbs
from newsletter.models import Impression, Issue, Item, ItemSource, Source

WORDS_PER_MINUTE = 230


def issue_posts(session: Session, issue: Issue, link_secret: str) -> list[dict]:
    """The issue's posts in slot order, as the page and the email show them. `href` is relative."""
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
            "is_teaser": item.is_teaser,
        }
        for imp, item in rows
    ]
