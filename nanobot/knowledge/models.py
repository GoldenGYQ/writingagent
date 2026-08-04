"""Stable data contracts for the Knowledge Runtime.

The compiler deliberately keeps a typed intermediate representation (IR)
between source files and Markdown pages.  Tools and future providers can
exchange this representation without coupling extraction to presentation.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, cast
from uuid import uuid4

PAGE_TYPES = (
    "entity",
    "concept",
    "source",
    "query",
    "comparison",
    "synthesis",
    "overview",
)
PAGE_DIRECTORIES = {
    "entity": "entities",
    "concept": "concepts",
    "source": "sources",
    "query": "queries",
    "comparison": "comparisons",
    "synthesis": "synthesis",
    "overview": "",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _text(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


@dataclass
class KnowledgeSource:
    """One source file discovered under a task's input directory."""

    path: str
    relative_path: str
    size: int = 0
    modified_at: str = ""
    sha256: str = ""
    kind: str = "text"
    status: str = "scanned"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> KnowledgeSource:
        return cls(
            path=_text(value.get("path")),
            relative_path=_text(value.get("relative_path")),
            size=value.get("size") if isinstance(value.get("size"), int) else 0,
            modified_at=_text(value.get("modified_at")),
            sha256=_text(value.get("sha256")),
            kind=_text(value.get("kind"), "text"),
            status=_text(value.get("status"), "scanned"),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass
class KnowledgePage:
    """A page draft in the Knowledge IR or a compiled wiki page."""

    type: str
    title: str
    slug: str
    body: str
    tags: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    source_path: str = ""
    created: str = field(default_factory=lambda: utc_now()[:10])
    updated: str = field(default_factory=lambda: utc_now()[:10])
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> KnowledgePage:
        page_type = _text(value.get("type"), "concept")
        return cls(
            type=page_type if page_type in PAGE_TYPES else "concept",
            title=_text(value.get("title")),
            slug=_text(value.get("slug")),
            body=_text(value.get("body")),
            tags=[item for item in _list(value.get("tags")) if isinstance(item, str)],
            related=[item for item in _list(value.get("related")) if isinstance(item, str)],
            sources=[item for item in _list(value.get("sources")) if isinstance(item, str)],
            source_path=_text(value.get("source_path")),
            created=_text(value.get("created"), utc_now()[:10]),
            updated=_text(value.get("updated"), utc_now()[:10]),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass
class KnowledgeRelation:
    """A typed edge with optional source evidence."""

    source: str
    target: str
    relation: str
    evidence: str = ""
    source_path: str = ""
    start_line: int | None = None
    end_line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> KnowledgeRelation:
        return cls(
            source=_text(value.get("source")),
            target=_text(value.get("target")),
            relation=_text(value.get("relation"), "related_to"),
            evidence=_text(value.get("evidence")),
            source_path=_text(value.get("source_path")),
            start_line=value.get("start_line") if isinstance(value.get("start_line"), int) else None,
            end_line=value.get("end_line") if isinstance(value.get("end_line"), int) else None,
        )


@dataclass
class KnowledgeEntity:
    """Optional convenience view used by callers that extract entities."""

    name: str
    type: str = "entity"
    description: str = ""
    source_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class KnowledgeIR:
    """Extraction output consumed by the compiler."""

    project_id: str
    source_path: str
    pages: list[KnowledgePage] = field(default_factory=list)
    relations: list[KnowledgeRelation] = field(default_factory=list)
    entities: list[KnowledgeEntity] = field(default_factory=list)
    notes: str = ""
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "pages": [page.to_dict() for page in self.pages],
            "relations": [relation.to_dict() for relation in self.relations],
            "entities": [entity.to_dict() for entity in self.entities],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> KnowledgeIR:
        raw_pages = value.get("pages")
        raw_relations = value.get("relations")
        raw_entities = value.get("entities")
        return cls(
            project_id=_text(value.get("project_id")),
            source_path=_text(value.get("source_path")),
            pages=[
                KnowledgePage.from_dict(cast(Mapping[str, Any], item))
                for item in _list(raw_pages)
                if isinstance(item, Mapping)
            ],
            relations=[
                KnowledgeRelation.from_dict(cast(Mapping[str, Any], item))
                for item in _list(raw_relations)
                if isinstance(item, Mapping)
            ],
            entities=[
                KnowledgeEntity(
                    name=_text(item.get("name")),
                    type=_text(item.get("type"), "entity"),
                    description=_text(item.get("description")),
                    source_path=_text(item.get("source_path")),
                    metadata=dict(item.get("metadata") or {}),
                )
                for item in _list(raw_entities)
                if isinstance(item, Mapping)
            ],
            notes=_text(value.get("notes")),
            created_at=_text(value.get("created_at"), utc_now()),
        )


@dataclass
class KnowledgeProject:
    """Workspace-level knowledge engineering task and published state."""

    id: str
    title: str
    source_root: str
    schema_name: str = "default"
    status: str = "active"
    phase: str = "scanning"
    sources: list[KnowledgeSource] = field(default_factory=list)
    ir_files: list[str] = field(default_factory=list)
    page_count: int = 0
    relation_count: int = 0
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "sources": [source.to_dict() for source in self.sources],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> KnowledgeProject:
        return cls(
            id=_text(value.get("id")),
            title=_text(value.get("title")),
            source_root=_text(value.get("source_root")),
            schema_name=_text(value.get("schema_name"), "default"),
            status=_text(value.get("status"), "active"),
            phase=_text(value.get("phase"), "scanning"),
            sources=[
                KnowledgeSource.from_dict(cast(Mapping[str, Any], item))
                for item in _list(value.get("sources"))
                if isinstance(item, Mapping)
            ],
            ir_files=[item for item in _list(value.get("ir_files")) if isinstance(item, str)],
            page_count=value.get("page_count") if isinstance(value.get("page_count"), int) else 0,
            relation_count=value.get("relation_count") if isinstance(value.get("relation_count"), int) else 0,
            created_at=_text(value.get("created_at"), utc_now()),
            updated_at=_text(value.get("updated_at"), utc_now()),
            metadata=dict(value.get("metadata") or {}),
        )

