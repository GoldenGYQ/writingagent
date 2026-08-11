"""Local vector index for published Knowledge chunks.

The first RAG implementation deliberately does not add FAISS/Chroma to the
The feature-hash backend remains a dependency-free fallback.  The optional
``fastembed`` backend provides real semantic embeddings without changing the
retriever or Agent Tool contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence, cast

from nanobot.knowledge.models import KnowledgeChunk

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    """Tokenize mixed English/Chinese text deterministically."""
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text.casefold()):
        token = match.group(0)
        tokens.append(token)
    for run in _CJK_RE.findall(text.casefold()):
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def hashed_vector(text: str, *, dimensions: int = 256) -> list[float]:
    """Create a normalized, deterministic feature-hashed vector."""
    values = [0.0] * max(32, dimensions)
    for token in tokenize(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % len(values)
        values[index] += 1.0
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        return values
    return [value / norm for value in values]


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    size = min(len(left), len(right))
    return sum(left[index] * right[index] for index in range(size))


@dataclass(frozen=True)
class VectorHit:
    chunk_id: str
    score: float


class LocalVectorStore:
    """Persist vectors as a compact JSON sidecar next to the Wiki index."""

    feature_hash_algorithm = "sha256-feature-hash-v1"

    def __init__(
        self,
        path: str | Path,
        *,
        dimensions: int = 256,
        backend: str = "feature_hash",
        model_name: str = "BAAI/bge-small-zh-v1.5",
    ) -> None:
        self.path = Path(path)
        self.dimensions = max(32, dimensions)
        self.backend = backend if backend in {"feature_hash", "fastembed"} else "feature_hash"
        self.model_name = model_name.strip() or "BAAI/bge-small-zh-v1.5"
        self._vectors: dict[str, list[float]] = {}

    @property
    def algorithm(self) -> str:
        if self.backend == "fastembed":
            return f"fastembed:{self.model_name}"
        return self.feature_hash_algorithm

    @property
    def available(self) -> bool:
        return bool(self._vectors)

    def load(self) -> int:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            self._vectors = {}
            return 0
        if not isinstance(payload, dict):
            self._vectors = {}
            return 0
        payload_value = cast(dict[str, Any], payload)
        if payload_value.get("algorithm") != self.algorithm:
            self._vectors = {}
            return 0
        values = payload_value.get("vectors")
        if not isinstance(values, list):
            self._vectors = {}
            return 0
        loaded: dict[str, list[float]] = {}
        for raw_item in cast(list[Any], values):
            if not isinstance(raw_item, dict):
                continue
            item = cast(dict[str, Any], raw_item)
            chunk_id = item.get("chunk_id")
            vector = item.get("vector")
            if not isinstance(chunk_id, str) or not isinstance(vector, list):
                continue
            vector_values = cast(list[Any], vector)
            if not all(isinstance(value, (int, float)) for value in vector_values):
                continue
            loaded[chunk_id] = [float(value) for value in cast(list[int | float], vector_values)]
        self._vectors = loaded
        return len(loaded)

    def _semantic_vectors(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError(
                "fastembed is not installed; install nanobot-ai[knowledge] or "
                "select embedding_backend='feature_hash'"
            ) from exc
        model = TextEmbedding(model_name=self.model_name)
        inputs = texts
        if query and self.model_name == "BAAI/bge-small-zh-v1.5":
            inputs = [f"为这个句子生成表示以用于检索相关文章：{text}" for text in texts]
        values = [[float(item) for item in vector] for vector in model.embed(inputs)]
        if values:
            self.dimensions = len(values[0])
        return values

    def _embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        if self.backend == "fastembed":
            return self._semantic_vectors(texts, query=query)
        return [hashed_vector(text, dimensions=self.dimensions) for text in texts]

    def build(self, chunks: Iterable[KnowledgeChunk]) -> int:
        values = list(chunks)
        vectors = self._embed([chunk.text for chunk in values])
        self._vectors = {
            chunk.id: vector
            for chunk, vector in zip(values, vectors, strict=True)
        }
        return len(self._vectors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "algorithm": self.algorithm,
            "dimensions": self.dimensions,
            "vectors": [
                {"chunk_id": chunk_id, "vector": vector}
                for chunk_id, vector in sorted(self._vectors.items())
            ],
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.replace(self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        allowed_ids: set[str] | None = None,
    ) -> list[VectorHit]:
        query_vector = self._embed([query], query=True)[0]
        hits = [
            VectorHit(chunk_id=chunk_id, score=max(0.0, cosine(query_vector, vector)))
            for chunk_id, vector in self._vectors.items()
            if allowed_ids is None or chunk_id in allowed_ids
        ]
        hits.sort(key=lambda item: (-item.score, item.chunk_id))
        return hits[: max(1, limit)]
