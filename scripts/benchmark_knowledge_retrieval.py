"""Compare vector index and reranking choices on a published Knowledge Wiki.

The experiment deliberately separates three concerns:

* BGE/FastEmbed creates semantic vectors.
* brute-force, FAISS, and Chroma search the same vectors.
* an optional BGE cross-encoder reranks the retrieved candidates.

This makes the results useful for architecture decisions instead of comparing
three concepts that live at different layers of a RAG system.
"""

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import argparse
import json
import math
import statistics
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from nanobot.knowledge.indexer import KnowledgeIndexer
from nanobot.knowledge.store import KnowledgeStore


class SearchBackend(Protocol):
    name: str
    index_bytes: int

    def search(self, vector: np.ndarray, limit: int) -> list[int]: ...


def _normalize(values: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return np.asarray(values / norms, dtype=np.float32)


class BruteForceBackend:
    name = "numpy-flat-ip"

    def __init__(self, vectors: np.ndarray) -> None:
        self.vectors = vectors
        self.index_bytes = int(vectors.nbytes)

    def search(self, vector: np.ndarray, limit: int) -> list[int]:
        scores = self.vectors @ vector
        return np.argsort(-scores, kind="stable")[:limit].tolist()


class FaissBackend:
    name = "faiss-index-flat-ip"

    def __init__(self, vectors: np.ndarray) -> None:
        import faiss

        self._faiss = faiss
        self.index = faiss.IndexFlatIP(vectors.shape[1])
        self.index.add(vectors)
        self.index_bytes = len(faiss.serialize_index(self.index))

    def search(self, vector: np.ndarray, limit: int) -> list[int]:
        _, indices = self.index.search(vector.reshape(1, -1), limit)
        return [int(value) for value in indices[0] if value >= 0]


class ChromaBackend:
    name = "chroma-hnsw-cosine"

    def __init__(self, vectors: np.ndarray, texts: list[str]) -> None:
        import chromadb
        from chromadb.config import Settings

        self._temporary = tempfile.TemporaryDirectory(
            prefix="nanobot-chroma-",
            ignore_cleanup_errors=True,
        )
        self._root = Path(self._temporary.name)
        client = chromadb.PersistentClient(
            path=str(self._root),
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = client.get_or_create_collection(
            "nanobot_benchmark",
            metadata={"hnsw:space": "cosine"},
        )
        ids = [f"chunk-{index:06d}" for index in range(len(texts))]
        self.collection.add(ids=ids, embeddings=vectors.tolist(), documents=texts)
        self.index_bytes = sum(path.stat().st_size for path in self._root.rglob("*") if path.is_file())

    def search(self, vector: np.ndarray, limit: int) -> list[int]:
        result = self.collection.query(query_embeddings=[vector.tolist()], n_results=limit)
        raw_ids = (result.get("ids") or [[]])[0]
        return [int(value.rsplit("-", 1)[-1]) for value in raw_ids]


@dataclass(frozen=True)
class EvalQuery:
    query: str
    relevant_paths: tuple[str, ...]


@dataclass
class BackendResult:
    backend: str
    build_ms: float
    query_p50_ms: float
    query_p95_ms: float
    index_bytes: int
    recall_at_k: float
    mrr_at_k: float
    ndcg_at_k: float
    reranker: str | None = None
    rerank_p50_ms: float | None = None


def _load_eval(path: Path) -> list[EvalQuery]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        EvalQuery(str(item["query"]), tuple(str(value) for value in item["relevant_paths"]))
        for item in payload["queries"]
    ]


def _collapse_paths(indices: list[int], paths: list[str], limit: int) -> list[str]:
    result: list[str] = []
    for index in indices:
        path = paths[index]
        if path not in result:
            result.append(path)
        if len(result) >= limit:
            break
    return result


def _quality(ranked: list[str], relevant: set[str], limit: int) -> tuple[float, float, float]:
    values = ranked[:limit]
    hits = [1 if path in relevant else 0 for path in values]
    recall = len(set(values) & relevant) / max(1, len(relevant))
    reciprocal = next((1.0 / rank for rank, hit in enumerate(hits, start=1) if hit), 0.0)
    dcg = sum(hit / math.log2(rank + 1) for rank, hit in enumerate(hits, start=1))
    ideal_hits = [1] * min(len(relevant), limit)
    ideal = sum(hit / math.log2(rank + 1) for rank, hit in enumerate(ideal_hits, start=1))
    return recall, reciprocal, dcg / ideal if ideal else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
    return ordered[index]


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Knowledge Retrieval Benchmark",
        "",
        f"- Project: `{payload['project_id']}`",
        f"- Documents / chunks: {payload['document_count']} / {payload['chunk_count']}",
        f"- Embedding: `{payload['embedding_model']}`",
        f"- Embedding wall time: {payload['embedding_ms']:.2f} ms",
        f"- Top-K: {payload['top_k']}",
        "",
        "| Backend | Build ms | Query p50 ms | Query p95 ms | Index bytes | Recall@K | MRR@K | nDCG@K | Reranker | Rerank p50 ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
    ]
    for result in payload["results"]:
        row = dict(result)
        row["reranker"] = result.get("reranker") or "none"
        row["rerank_p50_ms"] = (
            f"{result['rerank_p50_ms']:.2f}"
            if result.get("rerank_p50_ms") is not None
            else "-"
        )
        lines.append(
            "| {backend} | {build_ms:.2f} | {query_p50_ms:.3f} | {query_p95_ms:.3f} | "
            "{index_bytes} | {recall_at_k:.3f} | {mrr_at_k:.3f} | {ndcg_at_k:.3f} | "
            "{reranker} | {rerank_p50_ms} |".format(**row)
        )
    lines.extend((
        "",
        "> FAISS and Chroma consume the same BGE vectors. Differences in quality come from approximate search behavior or reranking, not from a different embedding model.",
        "",
    ))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--eval", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--embedding-model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--repeat", type=int, default=20)
    parser.add_argument("--reranker", action="store_true")
    args = parser.parse_args()

    store = KnowledgeStore(args.workspace)
    project = store.get_project(args.project_id)
    documents, chunks = KnowledgeIndexer(store).read_corpus(project)
    if not chunks:
        raise SystemExit("No published Wiki chunks found")
    evaluations = _load_eval(args.eval)
    texts = [chunk.text for chunk in chunks]
    paths = [chunk.path for chunk in chunks]

    started = time.perf_counter()
    if args.embedding_model == "feature-hash":
        from nanobot.knowledge.vector_store import hashed_vector

        vectors = _normalize(np.asarray([hashed_vector(text) for text in texts], dtype=np.float32))
        query_vectors = _normalize(np.asarray(
            [hashed_vector(item.query) for item in evaluations],
            dtype=np.float32,
        ))
    else:
        from fastembed import TextEmbedding

        embedding = TextEmbedding(model_name=args.embedding_model)
        vectors = _normalize(np.asarray(list(embedding.embed(texts)), dtype=np.float32))
        query_texts = [f"为这个句子生成表示以用于检索相关文章：{item.query}" for item in evaluations]
        query_vectors = _normalize(np.asarray(list(embedding.embed(query_texts)), dtype=np.float32))
    embedding_ms = (time.perf_counter() - started) * 1000

    backends: list[tuple[SearchBackend, float]] = []
    backend_factories: list[Callable[[], SearchBackend]] = [
        lambda: BruteForceBackend(vectors),
        lambda: FaissBackend(vectors),
        lambda: ChromaBackend(vectors, texts),
    ]
    for factory in backend_factories:
        started = time.perf_counter()
        backend = factory()
        backends.append((backend, (time.perf_counter() - started) * 1000))

    cross_encoder = None
    reranker_name = None
    if args.reranker:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        reranker_name = "BAAI/bge-reranker-base"
        cross_encoder = TextCrossEncoder(model_name=reranker_name)

    results: list[BackendResult] = []
    details: dict[str, Any] = {}
    for backend, build_ms in backends:
        latencies: list[float] = []
        quality: list[tuple[float, float, float]] = []
        rerank_latencies: list[float] = []
        query_details: list[dict[str, Any]] = []
        for eval_index, evaluation in enumerate(evaluations):
            indices: list[int] = []
            for _ in range(max(1, args.repeat)):
                started = time.perf_counter()
                indices = backend.search(query_vectors[eval_index], max(args.top_k * 4, 20))
                latencies.append((time.perf_counter() - started) * 1000)
            if cross_encoder is not None:
                started = time.perf_counter()
                scores = list(cross_encoder.rerank(
                    evaluation.query,
                    [texts[index] for index in indices],
                ))
                rerank_latencies.append((time.perf_counter() - started) * 1000)
                indices = [
                    index
                    for _, index in sorted(zip(scores, indices, strict=True), reverse=True)
                ]
            ranked_paths = _collapse_paths(indices, paths, args.top_k)
            query_quality = _quality(ranked_paths, set(evaluation.relevant_paths), args.top_k)
            quality.append(query_quality)
            query_details.append({
                "query": evaluation.query,
                "ranked_paths": ranked_paths,
                "relevant_paths": list(evaluation.relevant_paths),
                "recall": query_quality[0],
                "mrr": query_quality[1],
                "ndcg": query_quality[2],
            })
        result = BackendResult(
            backend=backend.name,
            build_ms=round(build_ms, 3),
            query_p50_ms=round(statistics.median(latencies), 4),
            query_p95_ms=round(_percentile(latencies, 0.95), 4),
            index_bytes=backend.index_bytes,
            recall_at_k=round(statistics.mean(item[0] for item in quality), 6),
            mrr_at_k=round(statistics.mean(item[1] for item in quality), 6),
            ndcg_at_k=round(statistics.mean(item[2] for item in quality), 6),
            reranker=reranker_name,
            rerank_p50_ms=(round(statistics.median(rerank_latencies), 3) if rerank_latencies else None),
        )
        results.append(result)
        details[backend.name] = query_details

    payload = {
        "version": 1,
        "workspace": str(Path(args.workspace).resolve()),
        "project_id": args.project_id,
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "query_count": len(evaluations),
        "embedding_model": args.embedding_model,
        "embedding_ms": round(embedding_ms, 3),
        "top_k": args.top_k,
        "repeat": args.repeat,
        "results": [asdict(result) for result in results],
        "details": details,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
