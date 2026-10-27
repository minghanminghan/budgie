import json

import numpy as np
import pyarrow.parquet as pq

from newsletter.export import export_parquet
from newsletter.models import Embedding


def test_export_roundtrip(engine, session, make_source, make_item, now, tmp_path):
    src = make_source()
    a, _ = make_item(src), make_item(src)
    vec = np.array([0.1, 0.2, 0.3, 0.4], dtype="<f4")
    session.add(Embedding(item_id=a.id, model="intfloat/e5-base-v2", dim=4, vector=vec.tobytes(), created_at=now))
    session.commit()
    paths = export_parquet(engine, tmp_path)
    assert sorted(p.name for p in paths) == sorted(
        f"{t}.parquet" for t in ("items", "impressions", "events", "embeddings", "sources"))
    assert pq.read_table(tmp_path / "items.parquet").num_rows == 2
    assert pq.read_table(tmp_path / "impressions.parquet").num_rows == 0
    emb = pq.read_table(tmp_path / "embeddings.parquet")
    assert emb.num_rows == 1
    assert str(emb.schema.field("vector").type) == "list<element: float>"
    got = emb.column("vector").to_pylist()[0]
    assert len(got) == 4 and np.allclose(got, vec)
    sources = pq.read_table(tmp_path / "sources.parquet")
    assert json.loads(sources.column("config").to_pylist()[0]) == src.config
