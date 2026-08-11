"""Build and load an incremental, published-Wiki retrieval index."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, cast

from nanobot.knowledge.compiler import parse_frontmatter
from nanobot.knowledge.models import KnowledgeChunk, KnowledgeDocument, KnowledgeProject
from nanobot.knowledge.store import KnowledgeStore
from nanobot.knowledge.vector_store import LocalVectorStore

_SKIP_PAGES = {"index.md", "overview.md", "log.md"}
_MAX_CHUNK_CHARS = 2_400


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in cast(list[Any], value)] if isinstance(value, list) else []


def _document_from_dict(value: dict[str, Any]) -> KnowledgeDocument:
    return KnowledgeDocument(
        id=str(value.get("id") or ""),
        project_id=str(value.get("project_id") or ""),
        path=str(value.get("path") or ""),
        title=str(value.get("title") or ""),
        page_type=str(value.get("page_type") or "concept"),
        node_id=str(value.get("node_id") or ""),
        tags=_string_list(value.get("tags")),
        related=_string_list(value.get("related")),
        sources=_string_list(value.get("sources")),
        content_hash=str(value.get("content_hash") or ""),
        updated_at=str(value.get("updated_at") or ""),
        size=int(value.get("size") or 0),
        modified_ns=int(value.get("modified_ns") or 0),
    )


def _chunk_from_dict(value: dict[str, Any]) -> KnowledgeChunk:
    return KnowledgeChunk(
        id=str(value.get("id") or ""),
        document_id=str(value.get("document_id") or ""),
        project_id=str(value.get("project_id") or ""),
        path=str(value.get("path") or ""),
        title=str(value.get("title") or ""),
        page_type=str(value.get("page_type") or "concept"),
        text=str(value.get("text") or ""),
        start_line=int(value.get("start_line") or 1),
        end_line=int(value.get("end_line") or 1),
        heading=str(value.get("heading") or ""),
        node_id=str(value.get("node_id") or ""),
        tags=_string_list(value.get("tags")),
        sources=_string_list(value.get("sources")),
        content_hash=str(value.get("content_hash") or ""),
    )


def retrieval_root(store: KnowledgeStore, project_id: str) -> Path:
    return store.project_path(project_id) / "knowledge" / "retrieval"


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _frontmatter_lines(lines: list[str]) -> int:
    if not lines or lines[0].strip() != "---":
        return 0
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return index + 1
    return 0


def _chunk_sections(
    *,
    lines: list[str],
    offset: int,
    max_chars: int = _MAX_CHUNK_CHARS,
) -> Iterable[tuple[str, str, int, int]]:
    """Yield heading-aware chunks with stable source line anchors."""
    sections: list[tuple[str, int, list[str]]] = []
    heading = ""
    start_line = offset + 1
    buffer: list[str] = []
    for index, line in enumerate(lines, start=offset + 1):
        if line.lstrip().startswith("#"):
            if buffer and "\n".join(buffer).strip():
                sections.append((heading, start_line, buffer))
            heading = line.lstrip().lstrip("#").strip() or heading
            start_line = index
            buffer = [line]
        else:
            buffer.append(line)
    if buffer and "\n".join(buffer).strip():
        sections.append((heading, start_line, buffer))

    for section_heading, section_start, section_lines in sections:
        current: list[str] = []
        current_start = section_start
        current_length = 0
        for index, line in enumerate(section_lines, start=section_start):
            line_length = len(line) + 1
            if current and current_length + line_length > max_chars:
                text = "\n".join(current).strip()
                if text:
                    yield section_heading, text, current_start, index - 1
                current = []
                current_start = index
                current_length = 0
            current.append(line)
            current_length += line_length
        text = "\n".join(current).strip()
        if text:
            yield section_heading, text, current_start, section_start + len(section_lines) - 1


@dataclass
class KnowledgeIndexSnapshot:
    project_id: str
    documents: list[KnowledgeDocument]
    chunks: list[KnowledgeChunk]
    vectors: LocalVectorStore
    manifest: dict[str, Any]
    persisted: bool

    @property
    def vector_available(self) -> bool:
        return self.vectors.available


class KnowledgeIndexer:
    """Create a derived index without modifying Wiki Markdown."""

    def __init__(
        self,
        store: KnowledgeStore,
        *,
        dimensions: int = 256,
        embedding_backend: str = "feature_hash",
        embedding_model: str = "BAAI/bge-small-zh-v1.5",
    ) -> None:
        self.store = store
        self.dimensions = max(32, dimensions)
        self.embedding_backend = embedding_backend
        self.embedding_model = embedding_model

    def _vector_store(self, path: Path) -> LocalVectorStore:
        return LocalVectorStore(
            path,
            dimensions=self.dimensions,
            backend=self.embedding_backend,
            model_name=self.embedding_model,
        )

    def read_corpus(self, project: KnowledgeProject) -> tuple[list[KnowledgeDocument], list[KnowledgeChunk]]:
        """Read the published Wiki as documents and heading-aware chunks."""
        documents: list[KnowledgeDocument] = []
        chunks: list[KnowledgeChunk] = []
        seen_titles: set[tuple[str, str]] = set()
        root = self.store.wiki_root(project.id)
        if not root.exists():
            return documents, chunks
        for path in sorted(root.rglob("*.md")):
            if path.name in _SKIP_PAGES:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            metadata, body = parse_frontmatter(content)
            relative = path.relative_to(root).as_posix()
            page_type = str(metadata.get("type") or path.parent.name or "concept")
            title = str(metadata.get("title") or path.stem)
            title_key = (page_type.casefold(), title.strip().casefold())
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            tags = [str(value) for value in metadata.get("tags", []) if value]
            related = [str(value) for value in metadata.get("related", []) if value]
            sources = [str(value) for value in metadata.get("sources", []) if value]
            content_hash = _content_hash(content)
            try:
                stat = path.stat()
                size = stat.st_size
                modified_ns = stat.st_mtime_ns
            except OSError:
                size = len(content.encode("utf-8"))
                modified_ns = 0
            document_id = relative[:-3] if relative.endswith(".md") else relative
            node_id = path.stem
            document = KnowledgeDocument(
                id=document_id,
                project_id=project.id,
                path=relative,
                title=title,
                page_type=page_type,
                node_id=node_id,
                tags=tags,
                related=related,
                sources=sources,
                content_hash=content_hash,
                updated_at=str(metadata.get("updated") or ""),
                size=size,
                modified_ns=modified_ns,
            )
            documents.append(document)
            raw_lines = content.splitlines()
            offset = _frontmatter_lines(raw_lines)
            body_lines = body.splitlines()
            for sequence, (heading, text, start_line, end_line) in enumerate(
                _chunk_sections(lines=body_lines, offset=offset)
            ):
                chunk_id = hashlib.sha256(
                    f"{document_id}:{sequence}:{content_hash}".encode("utf-8")
                ).hexdigest()[:24]
                chunks.append(KnowledgeChunk(
                    id=chunk_id,
                    document_id=document_id,
                    project_id=project.id,
                    path=relative,
                    title=title,
                    page_type=page_type,
                    text=text[:_MAX_CHUNK_CHARS],
                    start_line=max(1, start_line),
                    end_line=max(start_line, end_line),
                    heading=heading,
                    node_id=node_id,
                    tags=tags,
                    sources=sources,
                    content_hash=content_hash,
                ))
        return documents, chunks

    def _paths(self, project_id: str) -> tuple[Path, Path, Path]:
        root = retrieval_root(self.store, project_id)
        return root / "manifest.json", root / "chunks.jsonl", root / "vectors.json"

    def _manifest_is_fresh(self, project: KnowledgeProject, manifest: dict[str, Any]) -> bool:
        expected_algorithm = self._vector_store(self._paths(project.id)[2]).algorithm
        if manifest.get("algorithm") != expected_algorithm:
            return False
        expected = manifest.get("documents")
        if not isinstance(expected, list):
            return False
        root = self.store.wiki_root(project.id)
        current_stats: dict[str, tuple[int, int]] = {}
        if not root.exists():
            return not expected
        for path in sorted(root.rglob("*.md")):
            if path.name in _SKIP_PAGES:
                continue
            try:
                stat = path.stat()
            except OSError:
                return False
            relative = path.relative_to(root).as_posix()
            document_id = relative[:-3] if relative.endswith(".md") else relative
            current_stats[document_id] = (stat.st_size, stat.st_mtime_ns)
        expected_stats: dict[str, tuple[int, int]] = {}
        expected_values = cast(list[Any], expected)
        for raw_item in expected_values:
            if not isinstance(raw_item, dict):
                return False
            item = cast(dict[str, Any], raw_item)
            document_id = str(item.get("id") or "")
            size = item.get("size")
            modified_ns = item.get("modified_ns")
            if not document_id or not isinstance(size, int) or not isinstance(modified_ns, int):
                # Manifests produced before the stat fields fall back to a
                # content-hash read, preserving backward compatibility.
                current, _ = self.read_corpus(project)
                current_hashes = {value.id: value.content_hash for value in current}
                expected_hashes = {
                    str(value.get("id")): str(value.get("content_hash"))
                    for raw_value in expected_values
                    if isinstance(raw_value, dict)
                    for value in [cast(dict[str, Any], raw_value)]
                }
                return current_hashes == expected_hashes
            expected_stats[document_id] = (size, modified_ns)
        return current_stats == expected_stats

    def _load_persisted(self, project: KnowledgeProject) -> KnowledgeIndexSnapshot | None:
        manifest_path, chunks_path, vectors_path = self._paths(project.id)
        try:
            raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(raw_manifest, dict):
                return None
            manifest = cast(dict[str, Any], raw_manifest)
            if not self._manifest_is_fresh(project, manifest):
                return None
            chunks: list[KnowledgeChunk] = []
            for line in chunks_path.read_text(encoding="utf-8").splitlines():
                value = json.loads(line)
                if isinstance(value, dict):
                    chunks.append(_chunk_from_dict(cast(dict[str, Any], value)))
            raw_documents = manifest.get("documents")
            documents = [
                _document_from_dict(cast(dict[str, Any], value))
                for value in cast(list[Any], raw_documents)
                if isinstance(value, dict)
            ] if isinstance(raw_documents, list) else []
            vectors = self._vector_store(vectors_path)
            vectors.load()
            if not chunks or not vectors.available:
                return None
            return KnowledgeIndexSnapshot(project.id, documents, chunks, vectors, manifest, True)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def build(self, project: KnowledgeProject, *, persist: bool = True) -> KnowledgeIndexSnapshot:
        documents, chunks = self.read_corpus(project)
        vectors = self._vector_store(self._paths(project.id)[2])
        vector_count = vectors.build(chunks)
        manifest: dict[str, Any] = {
            "version": 1,
            "algorithm": vectors.algorithm,
            "dimensions": self.dimensions,
            "project_id": project.id,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "document_count": len(documents),
            "chunk_count": len(chunks),
            "vector_count": vector_count,
            "documents": [document.to_dict() for document in documents],
        }
        if persist and project.metadata.get("read_only") is not True:
            manifest_path, chunks_path, _ = self._paths(project.id)
            self.store.write_derived_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            chunk_text = "".join(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n" for chunk in chunks)
            self.store.write_derived_text(chunks_path, chunk_text)
            vectors.save()
        return KnowledgeIndexSnapshot(project.id, documents, chunks, vectors, manifest, persist)

    def load_or_build(self, project: KnowledgeProject) -> KnowledgeIndexSnapshot:
        if project.metadata.get("read_only") is not True:
            loaded = self._load_persisted(project)
            if loaded is not None:
                return loaded
        return self.build(project, persist=project.metadata.get("read_only") is not True)
