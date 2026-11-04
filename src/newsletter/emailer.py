from datetime import date
from html import escape
from typing import Protocol

import resend
from sqlalchemy import select
from sqlalchemy.orm import Session

from newsletter.config import Settings
from newsletter.models import Issue
from newsletter.posts import issue_posts

FAILED = "Today's issue failed"


class Sender(Protocol):
    def send(self, to: str, subject: str, text: str, html: str) -> None: ...


class ResendSender:
    def __init__(self, api_key: str, from_addr: str):
        self.api_key = api_key
        self.from_addr = from_addr

    def send(self, to: str, subject: str, text: str, html: str) -> None:
        resend.api_key = self.api_key
        resend.Emails.send({"from": self.from_addr, "to": [to], "subject": subject, "text": text, "html": html})


def _meta(p: dict) -> str:
    # Same line as the page's issue.html.
    parts = [p["source"]] + ([p["author"]] if p["author"] else []) + [f"{p['minutes']} min"]
    return ", ".join(parts + (["subscribers only"] if p["is_teaser"] else []))


def compose_daily_email(issue: Issue | None, posts: list[dict], base_url: str) -> tuple[str, str, str]:
    """Returns (subject, text, html). Each post links through its `/c/…` click link, like the page."""
    root = base_url.rstrip("/")
    link = root + "/"
    if issue is None or issue.status != "ok":
        line = f"{FAILED}: {'no issue was built' if issue is None else issue.error or 'unknown error'}."
        return FAILED, f"{line} {link}", f'<p>{escape(line)} <a href="{escape(link)}">{escape(link)}</a></p>'
    if not posts:
        subject = "No new posts today"
    elif len(posts) == 1:
        subject = "Today's 1 post is ready"
    else:
        subject = f"Today's {len(posts)} posts are ready"
    text = "\n\n".join([f"{subject}: {link}"] + [f"{p['title']}\n{_meta(p)}\n{root}{p['href']}" for p in posts])
    html = f'<p><a href="{escape(link)}">{escape(subject)}</a></p>' + "".join(
        f'<p><a href="{escape(root + p["href"])}">{escape(p["title"])}</a><br>'
        f'<span style="color:#5B6B7A">{escape(_meta(p))}</span></p>'
        for p in posts
    )
    return subject, text, html


def send_daily_email(session: Session, today: date, sender: Sender, settings: Settings) -> str:
    issue = session.scalar(select(Issue).where(Issue.issue_date == today))
    ok = issue is not None and issue.status == "ok"
    posts = issue_posts(session, issue, settings.link_signing_secret) if ok else []
    subject, text, html = compose_daily_email(issue, posts, settings.base_url)
    sender.send(settings.email_to, subject, text, html)
    return subject
