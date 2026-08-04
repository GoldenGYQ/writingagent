"""Stable data contracts for the Writing Document Runtime.

The first implementation deliberately uses small dataclasses instead of a
second persistence framework.  Every object has an explicit ``to_dict`` /
``from_dict`` boundary so the storage format can later move to SQLite without
leaking JSON details into tools or the WebUI.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, cast
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _text(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _int(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


@dataclass
class Artifact:
    """A durable digital asset maintained by an Agent.

    A file is only one representation of an Artifact.  ``current_revision_id``
    and ``metadata`` are intentionally kept outside the file contents.
    """

    id: str
    title: str
    artifact_type: str = "document"
    project_id: str = ""
    document_id: str = ""
    status: str = "draft"
    current_revision_id: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Artifact:
        return cls(
            id=_text(value.get("id")),
            title=_text(value.get("title")),
            artifact_type=_text(value.get("artifact_type"), "document"),
            project_id=_text(value.get("project_id")),
            document_id=_text(value.get("document_id")),
            status=_text(value.get("status"), "draft"),
            current_revision_id=(
                _text(value.get("current_revision_id")) or None
            ),
            created_at=_text(value.get("created_at"), utc_now()),
            updated_at=_text(value.get("updated_at"), utc_now()),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass
class Chapter:
    """A semantic section of a Document, backed by one text representation."""

    id: str
    title: str
    order: int
    path: str
    status: str = "draft"
    current_revision_id: str | None = None
    summary: str = ""
    word_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Chapter:
        return cls(
            id=_text(value.get("id")),
            title=_text(value.get("title")),
            order=_int(value.get("order")),
            path=_text(value.get("path")),
            status=_text(value.get("status"), "draft"),
            current_revision_id=(
                _text(value.get("current_revision_id")) or None
            ),
            summary=_text(value.get("summary")),
            word_count=_int(value.get("word_count")),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass
class Document:
    """A complete writing document composed of ordered semantic chapters."""

    id: str
    title: str
    project_id: str
    artifact_id: str
    path: str
    chapters: list[Chapter] = field(default_factory=list)
    status: str = "draft"
    current_revision_id: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "chapters": [chapter.to_dict() for chapter in self.chapters],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Document:
        raw_chapters = value.get("chapters")
        raw_chapters_list = cast(list[Any], raw_chapters) if isinstance(raw_chapters, list) else []
        chapters = (
            [
                Chapter.from_dict(cast(Mapping[str, Any], item))
                for item in raw_chapters_list
                if isinstance(item, Mapping)
            ]
        )
        return cls(
            id=_text(value.get("id")),
            title=_text(value.get("title")),
            project_id=_text(value.get("project_id")),
            artifact_id=_text(value.get("artifact_id")),
            path=_text(value.get("path")),
            chapters=chapters,
            status=_text(value.get("status"), "draft"),
            current_revision_id=(
                _text(value.get("current_revision_id")) or None
            ),
            created_at=_text(value.get("created_at"), utc_now()),
            updated_at=_text(value.get("updated_at"), utc_now()),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass
class WritingProject:
    """A durable writing task and its document pointers."""

    id: str
    title: str
    goal: str = ""
    style: str = ""
    outline: list[dict[str, Any]] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> WritingProject:
        outline = value.get("outline")
        document_ids = value.get("document_ids")
        outline_list = cast(list[Any], outline) if isinstance(outline, list) else []
        document_ids_list = cast(list[Any], document_ids) if isinstance(document_ids, list) else []
        return cls(
            id=_text(value.get("id")),
            title=_text(value.get("title")),
            goal=_text(value.get("goal")),
            style=_text(value.get("style")),
            outline=[
                dict(cast(Mapping[str, Any], item))
                for item in outline_list
                if isinstance(item, Mapping)
            ],
            document_ids=[item for item in document_ids_list if isinstance(item, str)],
            created_at=_text(value.get("created_at"), utc_now()),
            updated_at=_text(value.get("updated_at"), utc_now()),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass
class Revision:
    """An immutable chapter snapshot created by applying a ChangeSet."""

    id: str
    artifact_id: str
    project_id: str
    document_id: str
    chapter_id: str
    number: int
    content: str
    content_hash: str
    base_revision_id: str | None = None
    author: str = "agent"
    reason: str = ""
    status: str = "approved"
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Revision:
        raw_content = _text(value.get("content"))
        return cls(
            id=_text(value.get("id")),
            artifact_id=_text(value.get("artifact_id")),
            project_id=_text(value.get("project_id")),
            document_id=_text(value.get("document_id")),
            chapter_id=_text(value.get("chapter_id")),
            number=_int(value.get("number"), 1),
            content=raw_content,
            content_hash=_text(value.get("content_hash"), content_hash(raw_content)),
            base_revision_id=(
                _text(value.get("base_revision_id")) or None
            ),
            author=_text(value.get("author"), "agent"),
            reason=_text(value.get("reason")),
            status=_text(value.get("status"), "approved"),
            created_at=_text(value.get("created_at"), utc_now()),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass
class Change:
    """One bounded change summary carried by a ChangeSet."""

    path: str
    before_hash: str
    after_hash: str
    unified_diff: str
    added: int
    deleted: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Change:
        return cls(
            path=_text(value.get("path")),
            before_hash=_text(value.get("before_hash")),
            after_hash=_text(value.get("after_hash")),
            unified_diff=_text(value.get("unified_diff")),
            added=_int(value.get("added")),
            deleted=_int(value.get("deleted")),
        )


@dataclass
class ChangeSet:
    """A proposed semantic modification, before it becomes a Revision."""

    id: str
    artifact_id: str
    document_id: str
    chapter_id: str
    base_revision_id: str | None
    proposed_content: str
    changes: list[Change]
    reason: str
    impact: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    status: str = "review"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    approved_at: str | None = None
    applied_revision_id: str | None = None
    feedback: str = ""
    reviewed_at: str | None = None
    reviewed_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "changes": [change.to_dict() for change in self.changes],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ChangeSet:
        raw_changes = value.get("changes")
        raw_changes_list = cast(list[Any], raw_changes) if isinstance(raw_changes, list) else []
        changes = (
            [
                Change.from_dict(cast(Mapping[str, Any], item))
                for item in raw_changes_list
                if isinstance(item, Mapping)
            ]
        )
        return cls(
            id=_text(value.get("id")),
            artifact_id=_text(value.get("artifact_id")),
            document_id=_text(value.get("document_id")),
            chapter_id=_text(value.get("chapter_id")),
            base_revision_id=_text(value.get("base_revision_id")) or None,
            proposed_content=_text(value.get("proposed_content")),
            changes=changes,
            reason=_text(value.get("reason")),
            impact=_text(value.get("impact")),
            sources=[
                dict(cast(Mapping[str, Any], item))
                for item in value.get("sources", [])
                if isinstance(item, Mapping)
            ]
            if isinstance(value.get("sources"), list)
            else [],
            status=_text(value.get("status"), "review"),
            created_at=_text(value.get("created_at"), utc_now()),
            updated_at=_text(value.get("updated_at"), utc_now()),
            approved_at=_text(value.get("approved_at")) or None,
            applied_revision_id=_text(value.get("applied_revision_id")) or None,
            feedback=_text(value.get("feedback")),
            reviewed_at=_text(value.get("reviewed_at")) or None,
            reviewed_by=_text(value.get("reviewed_by")) or None,
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass
class ReviewIssue:
    """A structured issue anchored to a chapter and optional Revision."""

    id: str
    document_id: str
    chapter_id: str
    revision_id: str | None
    kind: str
    severity: str
    description: str
    suggestion: str = ""
    start_line: int | None = None
    end_line: int | None = None
    changeset_id: str | None = None
    decision: str | None = None
    status: str = "open"
    sources: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ReviewIssue:
        raw_sources = value.get("sources")
        raw_sources_list = cast(list[Any], raw_sources) if isinstance(raw_sources, list) else []
        return cls(
            id=_text(value.get("id")),
            document_id=_text(value.get("document_id")),
            chapter_id=_text(value.get("chapter_id")),
            revision_id=_text(value.get("revision_id")) or None,
            kind=_text(value.get("kind"), "general"),
            severity=_text(value.get("severity"), "medium"),
            description=_text(value.get("description")),
            suggestion=_text(value.get("suggestion")),
            start_line=value.get("start_line") if isinstance(value.get("start_line"), int) else None,
            end_line=value.get("end_line") if isinstance(value.get("end_line"), int) else None,
            changeset_id=_text(value.get("changeset_id")) or None,
            decision=_text(value.get("decision")) or None,
            status=_text(value.get("status"), "open"),
            sources=[item for item in raw_sources_list if isinstance(item, str)],
            created_at=_text(value.get("created_at"), utc_now()),
            updated_at=_text(value.get("updated_at"), utc_now()),
        )
