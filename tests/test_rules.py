from dataclasses import replace

from newsletter.rank.base import Candidate
from newsletter.rank.rules import RulesRanker

BASE = Candidate(item_id=1, source_id=1, author=None, word_count=800, is_teaser=False,
                 age_hours=10.0, source_ups=0, source_downs=0, clicks=0)


def score(c):
    return RulesRanker().score([c])[0]


def test_better_source_ranks_higher():
    assert score(replace(BASE, source_ups=5)) > score(replace(BASE, source_downs=5))


def test_substantial_beats_tiny():
    assert score(replace(BASE, word_count=1500)) > score(replace(BASE, word_count=120))


def test_newer_beats_older():
    assert score(replace(BASE, age_hours=2)) > score(replace(BASE, age_hours=100))


def test_teaser_length_neutral():
    r = RulesRanker()
    assert r.length_term(replace(BASE, is_teaser=True, word_count=150)) == \
        r.length_term(replace(BASE, is_teaser=True, word_count=3000))
    assert score(replace(BASE, is_teaser=True, word_count=150)) == \
        score(replace(BASE, is_teaser=True, word_count=3000))


def test_zero_words_does_not_crash():
    assert score(replace(BASE, word_count=0)) >= 0


def test_version():
    assert RulesRanker().version == "rules-v1"
