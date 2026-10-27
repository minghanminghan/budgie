from datetime import date
from typing import Protocol

import resend
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from newsletter.config import Settings
from newsletter.models import Impression, Issue

FAILED = "Today's issue failed"


class Sender(Protocol):
    def send(self, to: str, subject: str, text: str) -> None: ...


class ResendSender:
    def __init__(self, api_key: str, from_addr: str):
        self.api_key = api_key
        self.from_addr = from_addr

    def send(self, to: str, subject: str, text: str) -> None:
        resend.api_key = self.api_key
        resend.Emails.send({"from": self.from_addr, "to": [to], "subject": subject, "text": text})


def compose_daily_email(issue: Issue | None, n_posts: int, base_url: str) -> tuple[str, str]:
    link = base_url.rstrip("/") + "/"
    if issue is None:
        return FAILED, f"{FAILED}: no issue was built. {link}"
    if issue.status != "ok":
        return FAILED, f"{FAILED}: {issue.error or 'unknown error'}. {link}"
    if n_posts == 0:
        subject = "No new posts today"
    elif n_posts == 1:
        subject = "Today's 1 post is ready"
    else:
        subject = f"Today's {n_posts} posts are ready"
    return subject, f"{subject}: {link}"


def send_daily_email(session: Session, today: date, sender: Sender, settings: Settings) -> str:
    issue = session.scalar(select(Issue).where(Issue.issue_date == today))
    n = 0
    if issue is not None:
        n = session.scalar(select(func.count()).select_from(Impression).where(Impression.issue_id == issue.id))
    subject, text = compose_daily_email(issue, n, settings.base_url)
    sender.send(settings.email_to, subject, text)
    return subject
