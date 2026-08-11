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
    return list(cast(list[Any], value)) if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def _int(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _float(value: Any, default: float | None = None) -> float | None:
    """Read a finite confidence value without turning arbitrary data into a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    result = float(value)
    return result if result == result and result not in {float("inf"), float("-inf")} else default


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 1 else None


@dataclass
class KnowledgeSource:
    """One source file discovered under a task's input directory."""

    path: str
    relative_path: str
    raw_relative_path: str = ""
    size: int = 0
    modified_at: str = ""
    sha256: str = ""
    kind: str = "text"
    status: str = "scanned"
    metadata: dict[str, Any] = field(default_factory=dict)
    ingestion_adapter: str = ""
    extraction_mode: str = ""
    requires_vision: bool = False
    bounded_read: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> KnowledgeSource:
        metadata = _dict(value.get("metadata"))
        return cls(
            path=_text(value.get("path")),
            relative_path=_text(value.get("relative_path")),
            raw_relative_path=_text(value.get("raw_relative_path")),
            size=_int(value.get("size")),
            modified_at=_text(value.get("modified_at")),
            sha256=_text(value.get("sha256")),
            kind=_text(value.get("kind"), "text"),
            status=_text(value.get("status"), "scanned"),
            metadata=metadata,
            ingestion_adapter=_text(
                value.get("ingestion_adapter"),
            ) or _text(metadata.get("ingestion_adapter")),
            extraction_mode=_text(
                value.get("extraction_mode"),
            ) or _text(metadata.get("extraction_mode")),
            requires_vision=bool(
                value.get("requires_vision")
                if isinstance(value.get("requires_vision"), bool)
                else metadata.get("requires_vision", False)
            ),
            bounded_read=_dict(value.get("bounded_read")),
        )


@dataclass
class KnowledgeEvidence:
    """A bounded, source-addressable observation supporting a knowledge fact."""

    id: str = field(default_factory=lambda: new_id("evidence"))
    source_path: str = ""
    page_number: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    quote: str = ""
    image_path: str = ""
    extraction_method: str = ""
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> KnowledgeEvidence:
        return cls(
            id=_text(value.get("id"), new_id("evidence")),
            source_path=_text(value.get("source_path") or value.get("path")),
            page_number=_optional_int(value.get("page_number")),
            start_line=_optional_int(value.get("start_line")),
            end_line=_optional_int(value.get("end_line")),
            quote=_text(value.get("quote"))[:20_000],
            image_path=_text(value.get("image_path")),
            extraction_method=_text(value.get("extraction_method")),
            confidence=_float(value.get("confidence")),
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
    evidence: list[KnowledgeEvidence] = field(default_factory=list)

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
            evidence=[
                KnowledgeEvidence.from_dict(cast(Mapping[str, Any], item))
                for item in _list(value.get("evidence"))
                if isinstance(item, Mapping)
            ],
        )


@dataclass
class KnowledgeRelation:
    """A typed edge with optional source evidence."""

    source: str
    target: str
    relation: str
    evidence: str = ""
    source_path: str = ""
    page_number: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    evidence_refs: list[KnowledgeEvidence] = field(default_factory=list)
    confidence: float | None = None

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
            page_number=_optional_int(value.get("page_number")),
            start_line=value.get("start_line") if isinstance(value.get("start_line"), int) else None,
            end_line=value.get("end_line") if isinstance(value.get("end_line"), int) else None,
            evidence_refs=[
                KnowledgeEvidence.from_dict(cast(Mapping[str, Any], item))
                for item in _list(value.get("evidence_refs") or value.get("evidence_items"))
                if isinstance(item, Mapping)
            ],
            confidence=_float(value.get("confidence")),
        )


@dataclass
class KnowledgeClaim:
    """A fact-level assertion whose provenance can be reviewed independently."""

    subject: str
    predicate: str
    object: str
    evidence: list[KnowledgeEvidence] = field(default_factory=list)
    source_path: str = ""
    confidence: float | None = None
    status: str = "asserted"
    id: str = field(default_factory=lambda: new_id("claim"))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "evidence": [item.to_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> KnowledgeClaim:
        return cls(
            subject=_text(value.get("subject")),
            predicate=_text(value.get("predicate")),
            object=_text(value.get("object")),
            evidence=[
                KnowledgeEvidence.from_dict(cast(Mapping[str, Any], item))
                for item in _list(value.get("evidence"))
                if isinstance(item, Mapping)
            ],
            source_path=_text(value.get("source_path")),
            confidence=_float(value.get("confidence")),
            status=_text(value.get("status"), "asserted"),
            id=_text(value.get("id"), new_id("claim")),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass
class KnowledgeEntity:
    """Optional convenience view used by callers that extract entities."""

    name: str
    type: str = "entity"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    source_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    evidence: list[KnowledgeEvidence] = field(default_factory=list)

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
    claims: list[KnowledgeClaim] = field(default_factory=list)
    evidence: list[KnowledgeEvidence] = field(default_factory=list)
    review_hints: list[dict[str, Any]] = field(default_factory=list)
    relation_confidence: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "pages": [page.to_dict() for page in self.pages],
            "relations": [relation.to_dict() for relation in self.relations],
            "entities": [entity.to_dict() for entity in self.entities],
            "claims": [claim.to_dict() for claim in self.claims],
            "evidence": [item.to_dict() for item in self.evidence],
            "review_hints": list(self.review_hints),
            "relation_confidence": dict(self.relation_confidence),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> KnowledgeIR:
        raw_pages = value.get("pages")
        raw_relations = value.get("relations")
        raw_entities = value.get("entities")
        raw_claims = value.get("claims")
        raw_evidence = value.get("evidence")
        raw_relation_confidence = value.get("relation_confidence")
        relation_confidence: dict[str, float] = {}
        if isinstance(raw_relation_confidence, Mapping):
            confidence_values = cast(Mapping[Any, Any], raw_relation_confidence)
            for key, raw_confidence in confidence_values.items():
                parsed_confidence = _float(raw_confidence)
                if parsed_confidence is not None:
                    relation_confidence[str(key)] = parsed_confidence
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
                    tags=[item for item in _list(item.get("tags")) if isinstance(item, str)],
                    related=[item for item in _list(item.get("related")) if isinstance(item, str)],
                    source_path=_text(item.get("source_path")),
                    metadata=_dict(item.get("metadata")),
                    evidence=[
                        KnowledgeEvidence.from_dict(cast(Mapping[str, Any], evidence))
                        for evidence in _list(item.get("evidence"))
                        if isinstance(evidence, Mapping)
                    ],
                )
                for raw_item in _list(raw_entities)
                if isinstance(raw_item, Mapping)
                for item in [cast(Mapping[str, Any], raw_item)]
            ],
            notes=_text(value.get("notes")),
            created_at=_text(value.get("created_at"), utc_now()),
            claims=[
                KnowledgeClaim.from_dict(cast(Mapping[str, Any], item))
                for item in _list(raw_claims)
                if isinstance(item, Mapping)
            ],
            evidence=[
                KnowledgeEvidence.from_dict(cast(Mapping[str, Any], item))
                for item in _list(raw_evidence)
                if isinstance(item, Mapping)
            ],
            review_hints=[
                _dict(item)
                for item in _list(value.get("review_hints"))
                if isinstance(item, Mapping)
            ],
            relation_confidence=relation_confidence,
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
    current_revision_id: str | None = None
    published_revision_id: str | None = None
    review_status: str = "unreviewed"

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
            page_count=_int(value.get("page_count")),
            relation_count=_int(value.get("relation_count")),
            created_at=_text(value.get("created_at"), utc_now()),
            updated_at=_text(value.get("updated_at"), utc_now()),
            metadata=dict(value.get("metadata") or {}),
            current_revision_id=_text(value.get("current_revision_id")) or None,
            published_revision_id=_text(value.get("published_revision_id")) or None,
            review_status=_text(value.get("review_status"), "unreviewed"),
        )


@dataclass
class KnowledgeTask:
    """Durable, resumable workflow state for one Knowledge project."""

    id: str
    project_id: str
    kind: str = "knowledge_generation"
    status: str = "active"
    phase: str = "scanning"
    source_root: str = ""
    schema_name: str = "default"
    pending_sources: list[str] = field(default_factory=list)
    completed_sources: list[str] = field(default_factory=list)
    last_error: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> KnowledgeTask:
        return cls(
            id=_text(value.get("id")),
            project_id=_text(value.get("project_id")),
            kind=_text(value.get("kind"), "knowledge_generation"),
            status=_text(value.get("status"), "active"),
            phase=_text(value.get("phase"), "scanning"),
            source_root=_text(value.get("source_root")),
            schema_name=_text(value.get("schema_name"), "default"),
            pending_sources=[item for item in _list(value.get("pending_sources")) if isinstance(item, str)],
            completed_sources=[item for item in _list(value.get("completed_sources")) if isinstance(item, str)],
            last_error=_text(value.get("last_error")),
            created_at=_text(value.get("created_at"), utc_now()),
            updated_at=_text(value.get("updated_at"), utc_now()),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass
class KnowledgeReview:
    """Durable review gate created from a validation pass."""

    id: str
    project_id: str
    status: str
    checked_pages: int = 0
    issues: list[KnowledgeReviewIssue | dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    changeset_id: str | None = None
    approved_at: str | None = None
    approved_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "status": self.status,
            "checked_pages": self.checked_pages,
            "issues": [
                issue.to_dict() if isinstance(issue, KnowledgeReviewIssue) else dict(issue)
                for issue in self.issues
            ],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "changeset_id": self.changeset_id,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> KnowledgeReview:
        raw_issues = value.get("issues")
        return cls(
            id=_text(value.get("id")),
            project_id=_text(value.get("project_id")),
            status=_text(value.get("status"), "needs_changes"),
            checked_pages=_int(value.get("checked_pages")),
            issues=[
                KnowledgeReviewIssue.from_dict(cast(Mapping[str, Any], item))
                for item in _list(raw_issues)
                if isinstance(item, Mapping)
            ],
            created_at=_text(value.get("created_at"), utc_now()),
            updated_at=_text(value.get("updated_at"), _text(value.get("created_at"), utc_now())),
            changeset_id=_text(value.get("changeset_id")) or None,
            approved_at=_text(value.get("approved_at")) or None,
            approved_by=_text(value.get("approved_by")),
        )


@dataclass
class KnowledgeReviewIssue:
    """A durable, actionable issue produced by validation or review."""

    id: str = field(default_factory=lambda: new_id("issue"))
    kind: str = "suggestion"
    severity: str = "medium"
    status: str = "open"
    title: str = ""
    summary: str = ""
    source_refs: list[str] = field(default_factory=list)
    page_refs: list[str] = field(default_factory=list)
    evidence: list[KnowledgeEvidence] = field(default_factory=list)
    search_keywords: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    resolution: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "evidence": [item.to_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> KnowledgeReviewIssue:
        return cls(
            id=_text(value.get("id"), new_id("issue")),
            kind=_text(value.get("kind"), "suggestion"),
            severity=_text(value.get("severity"), "medium"),
            status=_text(value.get("status"), "open"),
            title=_text(value.get("title") or value.get("message")),
            summary=_text(value.get("summary") or value.get("message")),
            source_refs=[str(item) for item in _list(value.get("source_refs") or value.get("sources")) if str(item).strip()],
            page_refs=[str(item) for item in _list(value.get("page_refs") or value.get("pages")) if str(item).strip()],
            evidence=[
                KnowledgeEvidence.from_dict(cast(Mapping[str, Any], item))
                for item in _list(value.get("evidence"))
                if isinstance(item, Mapping)
            ],
            search_keywords=[str(item) for item in _list(value.get("search_keywords")) if str(item).strip()],
            actions=[str(item) for item in _list(value.get("actions")) if str(item).strip()],
            resolution=_text(value.get("resolution")),
            created_at=_text(value.get("created_at"), utc_now()),
            updated_at=_text(value.get("updated_at"), utc_now()),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass
class KnowledgeChangeSet:
    """A candidate knowledge update waiting for explicit human approval."""

    id: str
    project_id: str
    base_revision_id: str | None = None
    changes: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    status: str = "review"
    review_id: str | None = None
    feedback: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    approved_at: str | None = None
    reviewed_at: str | None = None
    reviewed_by: str = ""
    applied_revision_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> KnowledgeChangeSet:
        return cls(
            id=_text(value.get("id"), new_id("kb_changeset")),
            project_id=_text(value.get("project_id")),
            base_revision_id=_text(value.get("base_revision_id")) or None,
            changes=[_dict(item) for item in _list(value.get("changes")) if isinstance(item, Mapping)],
            reason=_text(value.get("reason")),
            status=_text(value.get("status"), "review"),
            review_id=_text(value.get("review_id")) or None,
            feedback=_text(value.get("feedback")),
            created_at=_text(value.get("created_at"), utc_now()),
            updated_at=_text(value.get("updated_at"), utc_now()),
            approved_at=_text(value.get("approved_at")) or None,
            reviewed_at=_text(value.get("reviewed_at")) or None,
            reviewed_by=_text(value.get("reviewed_by")),
            applied_revision_id=_text(value.get("applied_revision_id")) or None,
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass
class KnowledgeDocument:
    """A published Wiki page projected into the retrieval index."""

    id: str
    project_id: str
    path: str
    title: str
    page_type: str = "concept"
    node_id: str = ""
    tags: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    content_hash: str = ""
    updated_at: str = ""
    size: int = 0
    modified_ns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class KnowledgeChunk:
    """A bounded Markdown section used by lexical/vector retrieval."""

    id: str
    document_id: str
    project_id: str
    path: str
    title: str
    page_type: str
    text: str
    start_line: int
    end_line: int
    heading: str = ""
    node_id: str = ""
    tags: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class KnowledgeSearchResult:
    """Stable, bounded result contract shared by the tool and future UI."""

    project_id: str
    query: str
    mode: str
    documents: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    retrieval: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 2,
            "project_id": self.project_id,
            "query": self.query,
            "mode": self.mode,
            "documents": list(self.documents),
            "relations": list(self.relations),
            "citations": list(self.citations),
            "retrieval": dict(self.retrieval),
        }
