from datetime import date, datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
GO_LIVE = time(7, 0)


def issue_date_for(now: datetime) -> date:
    """The issue date for an instant: its calendar date in US Eastern time."""
    return now.astimezone(ET).date()


def go_live_at(d: date) -> datetime:
    return datetime.combine(d, GO_LIVE, tzinfo=ET)
