from datetime import UTC, date, datetime

from newsletter.clock import go_live_at, issue_date_for


def test_issue_date_for_dst_end():
    assert issue_date_for(datetime(2026, 11, 1, 3, 30, tzinfo=UTC)) == date(2026, 10, 31)


def test_issue_date_for_late_evening_et():
    # 23:30 EST on Nov 5 is 04:30 UTC on Nov 6
    assert issue_date_for(datetime(2026, 11, 6, 4, 30, tzinfo=UTC)) == date(2026, 11, 5)


def test_go_live_at_handles_dst():
    assert go_live_at(date(2026, 10, 30)).astimezone(UTC).hour == 11   # EDT
    assert go_live_at(date(2026, 11, 2)).astimezone(UTC).hour == 12    # EST
