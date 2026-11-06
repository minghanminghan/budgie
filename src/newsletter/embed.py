from datetime import UTC, datetime

import numpy as np
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from newsletter.models import Embedding, Item

MAX_SEQ_LENGTH = 512
# The model truncates to 512 tokens; trimming words first avoids tokenizing whole books.
MAX_WORDS = 1000


def passage_text(title: str, text: str) -> str:
    return f"passage: {title}\n{text}"


class OnnxModel:
    """The model repo's ONNX export (`onnx/`), mean-pooled like its sentence-transformers config."""

    def __init__(self, model_name: str):
        import onnxruntime
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        self.tokenizer = Tokenizer.from_file(hf_hub_download(model_name, "onnx/tokenizer.json"))
        self.tokenizer.enable_truncation(MAX_SEQ_LENGTH)
        self.tokenizer.enable_padding()
        self.session = onnxruntime.InferenceSession(hf_hub_download(model_name, "onnx/model.onnx"),
                                                    providers=["CPUExecutionProvider"])
        self.input_names = {i.name for i in self.session.get_inputs()}

    def encode(self, texts: list[str]) -> np.ndarray:
        enc = self.tokenizer.encode_batch(texts)
        inputs = {"input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                  "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
                  "token_type_ids": np.array([e.type_ids for e in enc], dtype=np.int64)}
        hidden = self.session.run(None, {k: v for k, v in inputs.items() if k in self.input_names})[0]
        mask = inputs["attention_mask"][:, :, None].astype(np.float32)
        return (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1e-9)


class Embedder:
    def __init__(self, model_name: str, model=None):
        self.model_name = model_name
        self._model = model

    @property
    def model(self):
        if self._model is None:
            self._model = OnnxModel(self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.asarray(self.model.encode(texts), dtype=np.float32)
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


def embed_pending(session: Session, embedder: Embedder, batch_size: int = 32) -> int:
    """Embed items with no vector for `embedder.model_name`. Commits per batch."""
    has_vector = exists().where(Embedding.item_id == Item.id, Embedding.model == embedder.model_name)
    total = 0
    while True:
        batch = session.scalars(select(Item).where(~has_vector).order_by(Item.id).limit(batch_size)).all()
        if not batch:
            return total
        texts = [passage_text(i.title, " ".join(i.text.split()[:MAX_WORDS])) for i in batch]
        vectors = embedder.embed(texts)
        now = datetime.now(UTC)
        for item, vec in zip(batch, vectors, strict=True):
            session.add(Embedding(item_id=item.id, model=embedder.model_name, dim=int(vec.shape[0]),
                                  vector=vec.astype("<f4").tobytes(), created_at=now))
        session.commit()
        total += len(batch)
