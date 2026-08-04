"""Atomic file-backed persistence for Writing Domain objects."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, cast

from nanobot.writing.models import (
    Artifact,
    ChangeSet,
    Document,
    ReviewIssue,
    Revision,
    WritingProject,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class WritingStoreError(RuntimeError):
    """Base error for invalid or unavailable Writing Store state."""


class WritingNotFoundError(WritingStoreError):
    """Raised when a requested writing asset does not exist."""


class WritingStore:
    """Persist writing assets under a workspace-controlled directory."""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.root = self.workspace / "writing"

    @staticmethod
    def validate_id(value: Any, label: str = "id") -> str:
        if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
            raise WritingStoreError(f"{label} is invalid")
        return value

    def _path(self, *parts: str) -> Path:
        for part in parts:
            self.validate_id(part, "path component")
        path = self.root.joinpath(*parts)
        resolved_root = self.root.resolve()
        resolved = path.resolve()
        if not resolved.is_relative_to(resolved_root):
            raise WritingStoreError("writing path escapes workspace")
        return path

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp:
            temp_path = Path(temp.name)
            temp.write(content)
            temp.flush()
            os.fsync(temp.fileno())
        try:
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @classmethod
    def _write_json(cls, path: Path, value: dict[str, Any]) -> None:
        cls._write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise WritingNotFoundError(f"writing asset not found: {path.name}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise WritingStoreError(f"cannot read writing asset {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise WritingStoreError(f"writing asset {path} must contain an object")
        return cast(dict[str, Any], value)

    def project_path(self, project_id: str) -> Path:
        return self._path(self.validate_id(project_id, "project_id"))

    def document_path(self, project_id: str, document_id: str) -> Path:
        return self._path(
            self.validate_id(project_id, "project_id"),
            "documents",
            self.validate_id(document_id, "document_id"),
        )

    def chapter_path(self, project_id: str, document_id: str, chapter_id: str) -> Path:
        return self.document_path(project_id, document_id) / "chapters" / f"{self.validate_id(chapter_id, 'chapter_id')}.md"

    def create_project(self, project: WritingProject) -> WritingProject:
        self.save_project(project)
        return project

    def save_project(self, project: WritingProject) -> None:
        path = self.project_path(project.id) / "project.json"
        self._write_json(path, project.to_dict())

    def get_project(self, project_id: str) -> WritingProject:
        return WritingProject.from_dict(
            self._read_json(self.project_path(project_id) / "project.json")
        )

    def save_document(self, document: Document) -> None:
        path = self.document_path(document.project_id, document.id) / "document.json"
        self._write_json(path, document.to_dict())

    def save_artifact(self, artifact: Artifact, *, document_id: str | None = None) -> None:
        target_document_id = document_id or artifact.document_id or artifact.id
        path = self.document_path(artifact.project_id, target_document_id) / "artifact.json"
        self._write_json(path, artifact.to_dict())

    def get_artifact(self, project_id: str, document_id: str) -> Artifact:
        return Artifact.from_dict(
            self._read_json(self.document_path(project_id, document_id) / "artifact.json")
        )

    def get_document(self, project_id: str, document_id: str) -> Document:
        return Document.from_dict(
            self._read_json(self.document_path(project_id, document_id) / "document.json")
        )

    def read_chapter(self, document: Document, chapter_id: str) -> str:
        chapter = next((item for item in document.chapters if item.id == chapter_id), None)
        if chapter is None:
            raise WritingNotFoundError(f"chapter not found: {chapter_id}")
        path = self.chapter_path(document.project_id, document.id, chapter.id)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except OSError as exc:
            raise WritingStoreError(f"cannot read chapter {chapter_id}: {exc}") from exc

    def write_chapter(self, document: Document, chapter_id: str, content: str) -> None:
        self.chapter_path(document.project_id, document.id, chapter_id).parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._write_text(
            self.chapter_path(document.project_id, document.id, chapter_id),
            content.replace("\r\n", "\n"),
        )

    def save_revision(self, revision: Revision) -> None:
        path = self.project_path(revision.project_id) / "revisions" / f"{self.validate_id(revision.id, 'revision_id')}.json"
        self._write_json(path, revision.to_dict())

    def get_revision(self, project_id: str, revision_id: str) -> Revision:
        return Revision.from_dict(
            self._read_json(self.project_path(project_id) / "revisions" / f"{self.validate_id(revision_id, 'revision_id')}.json")
        )

    def list_revisions(
        self,
        project_id: str,
        *,
        document_id: str | None = None,
        chapter_id: str | None = None,
    ) -> list[Revision]:
        directory = self.project_path(project_id) / "revisions"
        if not directory.exists():
            return []
        values: list[Revision] = []
        for path in sorted(directory.glob("*.json")):
            try:
                revision = Revision.from_dict(self._read_json(path))
            except WritingStoreError:
                continue
            if document_id and revision.document_id != document_id:
                continue
            if chapter_id and revision.chapter_id != chapter_id:
                continue
            values.append(revision)
        return sorted(values, key=lambda item: (item.chapter_id, item.number, item.created_at))

    def save_changeset(self, project_id: str, changeset: ChangeSet) -> None:
        path = self.project_path(project_id) / "changesets" / f"{self.validate_id(changeset.id, 'changeset_id')}.json"
        self._write_json(path, changeset.to_dict())

    def get_changeset(self, project_id: str, changeset_id: str) -> ChangeSet:
        return ChangeSet.from_dict(
            self._read_json(self.project_path(project_id) / "changesets" / f"{self.validate_id(changeset_id, 'changeset_id')}.json")
        )

    def list_changesets(self, project_id: str, *, document_id: str | None = None) -> list[ChangeSet]:
        directory = self.project_path(project_id) / "changesets"
        if not directory.exists():
            return []
        values: list[ChangeSet] = []
        for path in sorted(directory.glob("*.json")):
            try:
                changeset = ChangeSet.from_dict(self._read_json(path))
            except WritingStoreError:
                continue
            if document_id and changeset.document_id != document_id:
                continue
            values.append(changeset)
        return sorted(values, key=lambda item: item.created_at)

    def save_review(self, project_id: str, issue: ReviewIssue) -> None:
        path = self.project_path(project_id) / "reviews" / f"{self.validate_id(issue.id, 'review_id')}.json"
        self._write_json(path, issue.to_dict())

    def get_review(self, project_id: str, review_id: str) -> ReviewIssue:
        return ReviewIssue.from_dict(
            self._read_json(self.project_path(project_id) / "reviews" / f"{self.validate_id(review_id, 'review_id')}.json")
        )

    def list_reviews(
        self,
        project_id: str,
        *,
        document_id: str | None = None,
        chapter_id: str | None = None,
    ) -> list[ReviewIssue]:
        directory = self.project_path(project_id) / "reviews"
        if not directory.exists():
            return []
        values: list[ReviewIssue] = []
        for path in sorted(directory.glob("*.json")):
            try:
                issue = ReviewIssue.from_dict(self._read_json(path))
            except WritingStoreError:
                continue
            if document_id and issue.document_id != document_id:
                continue
            if chapter_id and issue.chapter_id != chapter_id:
                continue
            values.append(issue)
        return sorted(values, key=lambda item: item.created_at)
