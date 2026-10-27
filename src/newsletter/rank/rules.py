import math

from newsletter.rank.base import Candidate

SOURCE_WEIGHT = 0.45
LENGTH_WEIGHT = 0.35
RECENCY_WEIGHT = 0.20
LENGTH_MIDPOINT = 600       # words where the length term is 0.5
LENGTH_STEEPNESS = 2.0
RECENCY_TAU_HOURS = 72.0    # e-folding time of the recency decay
NEUTRAL = 0.5


class RulesRanker:
    """v1 ranker: source track record + length + recency, each in [0, 1]."""

    version = "rules-v1"

    @staticmethod
    def source_term(c: Candidate) -> float:
        return (c.source_ups + 1) / (c.source_ups + c.source_downs + 2)

    @staticmethod
    def length_term(c: Candidate) -> float:
        if c.is_teaser:
            return NEUTRAL
        x = math.log(max(c.word_count, 1) / LENGTH_MIDPOINT) * LENGTH_STEEPNESS
        return 1 / (1 + math.exp(-x))

    @staticmethod
    def recency_term(c: Candidate) -> float:
        return math.exp(-max(c.age_hours, 0.0) / RECENCY_TAU_HOURS)

    def score(self, candidates: list[Candidate]) -> list[float]:
        return [
            SOURCE_WEIGHT * self.source_term(c)
            + LENGTH_WEIGHT * self.length_term(c)
            + RECENCY_WEIGHT * self.recency_term(c)
            for c in candidates
        ]
