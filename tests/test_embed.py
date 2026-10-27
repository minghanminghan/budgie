import os

import numpy as np
import pytest
from sqlalchemy import select

from newsletter.embed import Embedder, embed_pending, passage_text
from newsletter.models import Embedding

MODEL = "intfloat/e5-base-v2"


class FakeModel:
    def __init__(self):
        self.seen: list[str] = []

    def encode(self, texts, normalize_embeddings=True, **kw):
        self.seen += texts
        out = np.array([[len(t), 1.0, 2.0, 3.0] for t in texts], dtype=np.float64)
        return out / np.linalg.norm(out, axis=1, keepdims=True)


def test_passage_text_prefix():
    assert passage_text("T", "body") == "passage: T\nbody"


def test_embed_pending_only_missing(session, make_source, make_item, now):
    src = make_source()
    items = [make_item(src) for _ in range(3)]
    session.add(Embedding(item_id=items[0].id, model=MODEL, dim=4, vector=b"\0" * 16, created_at=now))
    session.commit()
    fake = FakeModel()
    assert embed_pending(session, Embedder(MODEL, model=fake)) == 2
    assert len(fake.seen) == 2 and all(t.startswith("passage: ") for t in fake.seen)
    assert embed_pending(session, Embedder(MODEL, model=fake)) == 0


def test_vector_roundtrip(session, make_source, make_item):
    item = make_item(make_source(), title="Hello", text="world")
    session.commit()
    fake = FakeModel()
    embedder = Embedder(MODEL, model=fake)
    expected = embedder.embed([passage_text("Hello", "world")])[0]
    embed_pending(session, embedder)
    row = session.scalars(select(Embedding).where(Embedding.item_id == item.id)).one()
    assert row.model == "intfloat/e5-base-v2"
    assert row.dim == 4
    np.testing.assert_array_equal(np.frombuffer(row.vector, "<f4"), expected.astype("<f4"))


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("RUN_SLOW") != "1", reason="set RUN_SLOW=1 to load the real model")
def test_real_model_dim():
    out = Embedder(MODEL).embed([passage_text("Hello", "world")])
    assert out.shape == (1, 768)
    assert out.dtype == np.float32
