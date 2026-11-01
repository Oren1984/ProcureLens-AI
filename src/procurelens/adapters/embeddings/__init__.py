"""Local embedding providers (no paid API)."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class EmbeddingProvider(Protocol):
    model_id: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedProvider:
    """ONNX model via fastembed. Weights are downloaded once from Hugging Face into
    ``cache_dir`` (``qdrant/bge-small-en-v1.5-onnx-q`` for the default model) and reused."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        cache_dir: str | None = None,
        dim: int = 384,
    ) -> None:
        from fastembed import TextEmbedding  # heavy import kept lazy

        self.model_id = model_name
        self.dim = dim
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir, threads=2)
        self._query_prefix = (
            BGE_QUERY_PREFIX if "bge-" in model_name and "-en" in model_name else ""
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out = [v.tolist() for v in self._model.embed(texts, batch_size=16)]
        self._check(out)
        return out

    def embed_query(self, text: str) -> list[float]:
        out = [v.tolist() for v in self._model.embed([self._query_prefix + text])]
        self._check(out)
        return out[0]

    def _check(self, vectors: list[list[float]]) -> None:
        if any(len(v) != self.dim for v in vectors):
            raise ValueError(f"Embedding dimension mismatch for {self.model_id}")


class HashingEmbedder:
    """Deterministic bag-of-words hashing embedder for tests only (not semantic)."""

    model_id = "test-hashing-v1"
    dim = 384

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)
