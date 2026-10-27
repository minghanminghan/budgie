from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass
class Candidate:
    item_id: int
    source_id: int
    author: str | None
    word_count: int
    is_teaser: bool
    age_hours: float
    source_ups: int
    source_downs: int
    clicks: int

    def features(self) -> dict:
        out = asdict(self)
        del out["item_id"]
        return out


class Ranker(Protocol):
    version: str

    def score(self, candidates: list[Candidate]) -> list[float]: ...
