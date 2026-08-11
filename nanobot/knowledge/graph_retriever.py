"""Read-only graph expansion over the published ``graph.json`` snapshot."""

from __future__ import annotations

import json
from collections import deque
from typing import Any, Iterable, cast

from nanobot.knowledge.store import KnowledgeStore


class KnowledgeGraphRetriever:
    """Resolve Wiki node ids and expand a bounded undirected neighborhood."""

    def __init__(self, store: KnowledgeStore, project_id: str) -> None:
        self.store = store
        self.project_id = project_id
        self.graph_path = store.project_path(project_id) / "knowledge" / "graph" / "graph.json"
        self.graph = self._load()
        raw_nodes = self.graph.get("nodes")
        node_values = cast(list[Any], raw_nodes) if isinstance(raw_nodes, list) else []
        self.nodes: dict[str, dict[str, Any]] = {}
        for raw_node in node_values:
            if not isinstance(raw_node, dict):
                continue
            node = cast(dict[str, Any], raw_node)
            node_id = str(node.get("id") or "").strip()
            if node_id:
                self.nodes[node_id] = dict(node)
        self.adjacency: dict[str, set[str]] = {node_id: set() for node_id in self.nodes}
        self.edge_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
        raw_edges = self.graph.get("edges")
        edge_values = cast(list[Any], raw_edges) if isinstance(raw_edges, list) else []
        for raw_value in edge_values:
            if not isinstance(raw_value, dict):
                continue
            value = cast(dict[str, Any], raw_value)
            source = str(value.get("source") or "").strip()
            target = str(value.get("target") or "").strip()
            if not source or not target or source == target:
                continue
            if source not in self.nodes or target not in self.nodes:
                continue
            first, second = sorted((source, target), key=str.casefold)
            pair = (first, second)
            self.adjacency[source].add(target)
            self.adjacency[target].add(source)
            existing = self.edge_by_pair.get(pair)
            if existing is None:
                self.edge_by_pair[pair] = dict(value)
                continue
            for key in ("relation", "source_path"):
                current = str(existing.get(key) or "").strip()
                incoming = str(value.get(key) or "").strip()
                if incoming and incoming != current:
                    existing[key] = " | ".join(item for item in (current, incoming) if item)
            current_evidence = str(existing.get("evidence") or "").strip()
            incoming_evidence = str(value.get("evidence") or "").strip()
            if incoming_evidence and incoming_evidence not in current_evidence:
                existing["evidence"] = "\n".join(item for item in (current_evidence, incoming_evidence) if item)
            raw_refs = existing.get("evidence_refs")
            refs: list[Any] = list(cast(list[Any], raw_refs)) if isinstance(raw_refs, list) else []
            incoming_refs = value.get("evidence_refs")
            if isinstance(incoming_refs, list):
                for ref in cast(list[Any], incoming_refs):
                    if ref not in refs:
                        refs.append(ref)
            if refs:
                existing["evidence_refs"] = refs[:20]
            if isinstance(value.get("confidence"), (int, float)):
                existing["confidence"] = max(float(existing.get("confidence", 0.0) or 0.0), float(value["confidence"]))

    def _load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.graph_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"nodes": [], "edges": [], "communities": []}
        return cast(dict[str, Any], value) if isinstance(value, dict) else {"nodes": [], "edges": [], "communities": []}

    def resolve(self, references: Iterable[str], *, limit: int = 5) -> list[str]:
        """Resolve slugs/titles to stable graph node ids."""
        values = [str(reference).strip().casefold() for reference in references if str(reference).strip()]
        result: list[str] = []
        for node_id, node in self.nodes.items():
            candidates = {
                node_id.casefold(),
                str(node.get("title") or "").strip().casefold(),
            }
            if any(value in candidates or any(value in candidate for candidate in candidates) for value in values):
                result.append(node_id)
        result.sort(key=lambda item: (-len(self.adjacency.get(item, set())), item.casefold()))
        return result[: max(1, limit)]

    def expand(
        self,
        seed_ids: Iterable[str],
        *,
        hops: int = 1,
        max_nodes: int = 24,
        max_relations: int = 40,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
        """Return related nodes, evidence-bearing edges and distance metadata."""
        bounded_hops = max(0, min(2, hops))
        seeds = [item for item in seed_ids if item in self.nodes]
        distances: dict[str, int] = {item: 0 for item in seeds}
        queue: deque[str] = deque(seeds)
        while queue:
            current = queue.popleft()
            distance = distances[current]
            if distance >= bounded_hops:
                continue
            for neighbor in sorted(self.adjacency.get(current, set()), key=str.casefold):
                if neighbor not in distances:
                    distances[neighbor] = distance + 1
                    queue.append(neighbor)
                if len(distances) >= max(1, max_nodes):
                    queue.clear()
                    break
        node_ids = sorted(distances, key=lambda item: (distances[item], item.casefold()))[:max_nodes]
        allowed = set(node_ids)
        relations: list[dict[str, Any]] = []
        for pair, edge in sorted(self.edge_by_pair.items(), key=lambda item: item[0]):
            if pair[0] not in allowed or pair[1] not in allowed:
                continue
            value = dict(edge)
            value["distance"] = min(distances[pair[0]], distances[pair[1]])
            relations.append(value)
            if len(relations) >= max(1, max_relations):
                break
        nodes = [
            {
                **self.nodes[node_id],
                "distance": distances[node_id],
                "is_seed": node_id in seeds,
            }
            for node_id in node_ids
        ]
        return nodes, relations, distances
