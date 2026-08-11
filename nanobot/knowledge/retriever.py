"""Hybrid retrieval over the published Wiki and its graph snapshot.

The retriever intentionally returns bounded metadata and snippets rather than
injecting an entire Wiki into a model context.  The vector backend is supplied
by :mod:`nanobot.knowledge.vector_store`; graph expansion remains a read-only
projection of the compiler's ``graph.json`` output.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from nanobot.knowledge.graph_retriever import KnowledgeGraphRetriever
from nanobot.knowledge.indexer import KnowledgeIndexer, KnowledgeIndexSnapshot
from nanobot.knowledge.models import KnowledgeProject, KnowledgeSearchResult
from nanobot.knowledge.store import KnowledgeStore
from nanobot.knowledge.vector_store import tokenize


def _lexical_score(query: str, text: str) -> float:
    """Score token overlap while giving exact phrase matches a small boost."""
    query_value = query.casefold().strip()
    text_value = text.casefold()
    if not query_value or not text_value:
        return 0.0
    query_tokens = set(tokenize(query_value))
    text_tokens = set(tokenize(text_value))
    overlap = len(query_tokens & text_tokens) / max(1, len(query_tokens))
    phrase_boost = 0.25 if query_value in text_value else 0.0
    return min(1.0, overlap + phrase_boost)


def _snippet(text: str, query: str, *, max_chars: int = 1_200) -> tuple[str, int, int]:
    lines = text.splitlines() or [text]
    needle = query.casefold().strip()
    hit = next((index for index, line in enumerate(lines) if needle in line.casefold()), 0)
    start = max(0, hit - 2)
    end = min(len(lines), hit + 5)
    value = "\n".join(lines[start:end])[:max_chars]
    return value, start + 1, end


class KnowledgeRetriever:
    """Bounded vector + lexical + graph retrieval for one Knowledge project."""

    def __init__(
        self,
        store: KnowledgeStore,
        *,
        dimensions: int = 256,
        embedding_backend: str = "feature_hash",
        embedding_model: str = "BAAI/bge-small-zh-v1.5",
    ) -> None:
        self.store = store
        self.indexer = KnowledgeIndexer(
            store,
            dimensions=dimensions,
            embedding_backend=embedding_backend,
            embedding_model=embedding_model,
        )

    @staticmethod
    def _allowed_chunks(
        snapshot: KnowledgeIndexSnapshot,
        *,
        page_type: str | None,
        tag: str | None,
        source_path: str | None,
    ) -> set[str]:
        type_filter = page_type.strip().casefold() if page_type else None
        tag_filter = tag.strip().casefold() if tag else None
        source_filter = source_path.strip() if source_path else None
        allowed: set[str] = set()
        for chunk in snapshot.chunks:
            if type_filter and chunk.page_type.casefold() != type_filter:
                continue
            if tag_filter and tag_filter not in {item.casefold() for item in chunk.tags}:
                continue
            if source_filter and source_filter not in chunk.sources:
                continue
            allowed.add(chunk.id)
        return allowed

    @staticmethod
    def _document_map(snapshot: KnowledgeIndexSnapshot) -> dict[str, Any]:
        return {document.id: document for document in snapshot.documents}

    def search(
        self,
        project: KnowledgeProject,
        query: str,
        *,
        mode: str = "hybrid",
        limit: int = 10,
        page_type: str | None = None,
        tag: str | None = None,
        source_path: str | None = None,
        expand_hops: int = 1,
    ) -> KnowledgeSearchResult:
        mode_value = mode if mode in {"vector", "graph", "hybrid"} else "hybrid"
        limit_value = max(1, min(20, limit))
        query_value = query.strip()
        snapshot = self.indexer.load_or_build(project)
        allowed_ids = self._allowed_chunks(
            snapshot,
            page_type=page_type,
            tag=tag,
            source_path=source_path,
        )
        chunk_by_id = {chunk.id: chunk for chunk in snapshot.chunks}
        lexical: dict[str, float] = {}
        for chunk_id in allowed_ids:
            chunk = chunk_by_id[chunk_id]
            lexical[chunk_id] = _lexical_score(
                query_value,
                " ".join((chunk.title, chunk.heading, chunk.text, " ".join(chunk.tags))),
            )
        vector_scores: dict[str, float] = {}
        if mode_value in {"vector", "hybrid"} and snapshot.vectors.available:
            for hit in snapshot.vectors.search(query_value, limit=max(40, limit_value * 6), allowed_ids=allowed_ids):
                vector_scores[hit.chunk_id] = hit.score

        scores: dict[str, float] = {}
        candidates = set(vector_scores) | set(lexical)
        for chunk_id in candidates:
            vector_score = vector_scores.get(chunk_id, 0.0)
            lexical_score = lexical.get(chunk_id, 0.0)
            if mode_value == "vector":
                score = vector_score
            elif mode_value == "graph":
                score = lexical_score
            else:
                score = 0.65 * vector_score + 0.35 * lexical_score
            if score > 0:
                scores[chunk_id] = score

        ranked_chunks = sorted(
            scores,
            key=lambda item: (-scores[item], chunk_by_id[item].path.casefold(), chunk_by_id[item].start_line),
        )
        selected_chunks = [chunk_by_id[item] for item in ranked_chunks[: max(1, limit_value * 2)]]
        document_map = self._document_map(snapshot)
        by_document: dict[str, list[Any]] = defaultdict(list)
        for chunk in selected_chunks:
            by_document[chunk.document_id].append(chunk)

        documents: list[dict[str, Any]] = []
        for document_id, chunks in by_document.items():
            chunks.sort(key=lambda item: (-scores.get(item.id, 0.0), item.start_line))
            chunk = chunks[0]
            document = document_map[document_id]
            snippet, start_line, end_line = _snippet(chunk.text, query_value)
            vector_score = max(vector_scores.get(item.id, 0.0) for item in chunks)
            lexical_score = max(lexical.get(item.id, 0.0) for item in chunks)
            documents.append({
                "id": document.id,
                "path": document.path,
                "title": document.title,
                "page_type": document.page_type,
                "type": document.page_type,
                "score": round(scores.get(chunk.id, 0.0), 6),
                "vector_score": round(vector_score, 6),
                "lexical_score": round(lexical_score, 6),
                "snippet": snippet,
                "quote": snippet,
                "start_line": chunk.start_line + start_line - 1,
                "end_line": min(chunk.end_line, chunk.start_line + end_line - 1),
                "heading": chunk.heading,
                "node_id": document.node_id or chunk.node_id,
                "tags": list(document.tags),
                "sources": list(document.sources),
                "related": list(document.related),
                "project_id": project.id,
            })
        documents.sort(key=lambda item: (-item["score"], str(item["path"]).casefold()))
        documents = documents[:limit_value]

        graph = KnowledgeGraphRetriever(self.store, project.id)
        seed_references = [item.get("node_id") or item.get("title", "") for item in documents]
        seed_ids = graph.resolve(seed_references, limit=limit_value)
        graph_nodes: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        if mode_value in {"graph", "hybrid"} and seed_ids:
            graph_nodes, relations, _ = graph.expand(seed_ids, hops=expand_hops)
            graph_scores = {node.get("id"): 1.0 / (1.0 + float(node.get("distance", 0))) for node in graph_nodes}
            for item in documents:
                item["graph_score"] = round(graph_scores.get(item.get("node_id"), 0.0), 6)
            if mode_value == "hybrid":
                for item in documents:
                    item["score"] = round(
                        0.55 * float(item.get("score", 0.0)) + 0.45 * float(item.get("graph_score", 0.0)),
                        6,
                    )
                documents.sort(key=lambda item: (-item["score"], str(item["path"]).casefold()))
        elif mode_value == "vector":
            for item in documents:
                item["graph_score"] = 0.0

        fallback = None
        if not snapshot.vectors.available:
            fallback = "lexical"
        elif mode_value == "graph" and not seed_ids:
            fallback = "lexical_seed"
        return KnowledgeSearchResult(
            project_id=project.id,
            query=query_value,
            mode=mode_value,
            documents=documents,
            relations=relations,
            retrieval={
                "index_algorithm": snapshot.vectors.algorithm,
                "vector_available": snapshot.vectors.available,
                "fallback": fallback,
                "indexed_documents": len(snapshot.documents),
                "indexed_chunks": len(snapshot.chunks),
                "seed_nodes": seed_ids,
                "expanded_hops": max(0, min(2, expand_hops)),
                "graph_nodes": len(graph_nodes),
                "graph_relations": len(relations),
            },
        )
