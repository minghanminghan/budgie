import logging
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from newsletter.clock import go_live_at, issue_date_for
from newsletter.models import Event, Impression, Issue, Item, ItemSource, Source
from newsletter.rank.base import Candidate, Ranker

log = logging.getLogger(__name__)

WINDOW = timedelta(days=7)
THUMB_TYPES = ("thumb_up", "thumb_down", "thumb_clear")
ERROR_MAX = 500


@dataclass
class Pick:
    item_id: int
    slot: int
    is_wildcard: bool
    score: float
    features: dict


def latest_thumbs(session: Session, item_ids: list[int] | None = None) -> dict[int, str]:
    """Current thumb per item ("up"/"down"); the latest thumb event wins, clears are omitted."""
    q = select(Event.item_id, Event.type).where(Event.type.in_(THUMB_TYPES), Event.item_id.is_not(None))
    if item_ids is not None:
        q = q.where(Event.item_id.in_(item_ids))
    current: dict[int, str] = {}
    for item_id, kind in session.execute(q.order_by(Event.created_at, Event.id)):
        current[item_id] = kind.removeprefix("thumb_")
    return {k: v for k, v in current.items() if v != "clear"}


def get_candidates(session: Session, now: datetime) -> list[Candidate]:
    active_link = (
        select(ItemSource.item_id, ItemSource.source_id, ItemSource.seen_at)
        .join(Source, Source.id == ItemSource.source_id)
        .where(Source.status == "active")
    )
    shown = exists().where(Impression.item_id == Item.id)
    ref = func.coalesce(Item.published_at, Item.first_seen_at)
    items = session.scalars(
        select(Item).where(~shown, ref >= now - WINDOW, Item.id.in_(active_link.with_only_columns(ItemSource.item_id)))
    ).all()
    if not items:
        return []

    first_source: dict[int, int] = {}
    for item_id, source_id, _ in session.execute(
        active_link.where(ItemSource.item_id.in_([i.id for i in items]))
        .order_by(ItemSource.seen_at, ItemSource.source_id)
    ):
        first_source.setdefault(item_id, source_id)

    ups: Counter[int] = Counter()
    downs: Counter[int] = Counter()
    thumbs = latest_thumbs(session)
    sources_of: dict[int, set[int]] = defaultdict(set)
    if thumbs:
        for item_id, source_id in session.execute(
            select(ItemSource.item_id, ItemSource.source_id).where(ItemSource.item_id.in_(list(thumbs)))
        ):
            sources_of[item_id].add(source_id)
    for item_id, value in thumbs.items():
        for source_id in sources_of[item_id]:
            (ups if value == "up" else downs)[source_id] += 1

    clicks = dict(session.execute(
        select(Event.item_id, func.count()).where(Event.type == "click", Event.item_id.is_not(None))
        .group_by(Event.item_id)
    ).all())

    out = []
    for item in items:
        sid = first_source[item.id]
        age = (now - (item.published_at or item.first_seen_at)).total_seconds() / 3600
        out.append(Candidate(
            item_id=item.id, source_id=sid, author=item.author, word_count=item.word_count,
            is_teaser=item.is_teaser, age_hours=round(age, 3), source_ups=ups[sid],
            source_downs=downs[sid], clicks=clicks.get(item.id, 0),
        ))
    return out


def compose_issue(cands: list[Candidate], scores: list[float], rng: random.Random,
                  max_posts: int = 20, per_source_cap: int = 3) -> list[Pick]:
    order = sorted(range(len(cands)), key=lambda i: (-scores[i], -cands[i].clicks, cands[i].item_id))
    per_source: Counter[int] = Counter()
    passing = []
    for i in order:
        if per_source[cands[i].source_id] < per_source_cap:
            per_source[cands[i].source_id] += 1
            passing.append(i)

    wild: list[int] = []
    if len(passing) <= max_posts:
        chosen = passing
    else:
        k = rng.choice([2, 3])
        chosen = passing[: max_posts - k]
        wild = rng.sample(passing[max_posts - k:], k)
        for w in wild:
            chosen.insert(rng.randint(0, len(chosen)), w)
    wild_set = set(wild)
    return [
        Pick(item_id=cands[i].item_id, slot=slot, is_wildcard=i in wild_set,
             score=float(scores[i]), features=cands[i].features())
        for slot, i in enumerate(chosen, start=1)
    ]


def _issue_for(session: Session, issue_date: date) -> Issue | None:
    return session.scalar(select(Issue).where(Issue.issue_date == issue_date))


def build_issue(session: Session, issue_date: date, ranker: Ranker, rng: random.Random,
                now: datetime) -> Issue:
    """Build and freeze the issue for `issue_date`. Idempotent; never raises.

    An existing ok issue is returned unchanged. A failed one is replaced.
    """
    existing = _issue_for(session, issue_date)
    if existing is not None and existing.status == "ok":
        return existing
    if existing is not None:
        session.delete(existing)
        session.commit()
    try:
        cands = get_candidates(session, now)
        scores = ranker.score(cands) if cands else []
        picks = compose_issue(cands, scores, rng)
        issue = Issue(issue_date=issue_date, built_at=now, status="ok", ranker_version=ranker.version)
        session.add(issue)
        session.flush()
        session.add_all(
            Impression(issue_id=issue.id, item_id=p.item_id, slot=p.slot, is_wildcard=p.is_wildcard,
                       score=p.score, features=p.features)
            for p in picks
        )
        session.commit()
        return issue
    except Exception as exc:
        session.rollback()
        log.exception("issue build for %s failed", issue_date)
        try:
            # A concurrent build may have won the race; keep its result.
            raced = _issue_for(session, issue_date)
            if raced is not None:
                return raced
            issue = Issue(issue_date=issue_date, built_at=now, status="failed",
                          ranker_version=getattr(ranker, "version", None),
                          error=f"{type(exc).__name__}: {exc}"[:ERROR_MAX])
            session.add(issue)
            session.commit()
            return issue
        except Exception:
            session.rollback()
            log.exception("could not record failed issue for %s", issue_date)
            return Issue(issue_date=issue_date, built_at=now, status="failed", error=str(exc)[:ERROR_MAX])


def visible_issue(session: Session, now: datetime) -> Issue | None:
    """The latest ok issue that has gone live (07:00 ET on its date)."""
    today = issue_date_for(now)
    last_live = today if now >= go_live_at(today) else today - timedelta(days=1)
    return session.scalar(
        select(Issue).where(Issue.status == "ok", Issue.issue_date <= last_live)
        .order_by(Issue.issue_date.desc()).limit(1)
    )
