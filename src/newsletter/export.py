import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import JSON, Engine, select

from newsletter.models import Base

TABLES = ("items", "impressions", "events", "embeddings", "sources")


def _column(table_name: str, col, values: list) -> pa.Array:
    if isinstance(col.type, JSON):
        return pa.array([None if v is None else json.dumps(v) for v in values], type=pa.string())
    if table_name == "embeddings" and col.name == "vector":
        return pa.array([np.frombuffer(v, "<f4").tolist() for v in values], type=pa.list_(pa.float32()))
    return pa.array(values)


def export_parquet(engine: Engine, out_dir: Path) -> list[Path]:
    """Write the training-relevant tables to Parquet, one file per table."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    with engine.connect() as conn:
        for name in TABLES:
            table = Base.metadata.tables[name]
            rows = conn.execute(select(table)).all()
            data = {c.name: _column(name, c, [r._mapping[c.name] for r in rows]) for c in table.columns}
            path = out_dir / f"{name}.parquet"
            pq.write_table(pa.table(data), path)
            paths.append(path)
    return paths
