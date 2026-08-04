"""Atomic workspace-scoped persistence for Knowledge Runtime artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, cast

from nanobot.knowledge.models import (
    KnowledgeIR,
    KnowledgePage,
    KnowledgeProject,
    KnowledgeReview,
    KnowledgeTask,
)

# Wiki slugs may be Chinese or other Unicode text.  They still must be a
# single safe path component and must not contain traversal or Windows-invalid
# filename characters.
_SAFE_ID = re.compile(r"^[^\x00-\x1f/\\:*?\"<>|]{1,128}$")


class KnowledgeStoreError(RuntimeError):
    """Base error for invalid Knowledge Store state."""


class KnowledgeNotFoundError(KnowledgeStoreError):
    """Raised when a Knowledge project or IR file is absent."""


class KnowledgeStore:
    """Persist projects under ``<workspace>/wikis/<project_id>``.

    The store never binds to a process-global workspace.  Callers create one
    per request, so a WebUI project scope cannot accidentally write into the
    Agent's default workspace.
    """

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.root = self.workspace / "wikis"

    @staticmethod
    def validate_id(value: Any, label: str = "id") -> str:
        if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
            raise KnowledgeStoreError(f"{label} is invalid")
        return value

    def _project_root(self, project_id: str) -> Path:
        project_id = self.validate_id(project_id, "project_id")
        root = self.root / project_id
        resolved_root = self.root.resolve()
        if not root.resolve().is_relative_to(resolved_root):
            raise KnowledgeStoreError("knowledge path escapes workspace")
        return root

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

    @staticmethod
    def _write_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="wb",
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
            raise KnowledgeNotFoundError(f"knowledge asset not found: {path.name}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise KnowledgeStoreError(f"cannot read knowledge asset {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise KnowledgeStoreError(f"knowledge asset {path} must contain an object")
        return cast(dict[str, Any], value)

    def project_path(self, project_id: str) -> Path:
        return self._project_root(project_id)

    def create_project(self, project: KnowledgeProject) -> KnowledgeProject:
        if self.project_path(project.id).exists():
            raise KnowledgeStoreError(f"knowledge project already exists: {project.id}")
        project_root = self.project_path(project.id)
        for relative in (
            "raw",
            "assets",
            "knowledge/ir",
            "knowledge/reviews",
            "knowledge/graph",
            "wiki/entities",
            "wiki/concepts",
            "wiki/sources",
            "wiki/synthesis",
            "wiki/queries",
            "wiki/comparisons",
        ):
            (project_root / relative).mkdir(parents=True, exist_ok=True)
        self.save_project(project)
        self._write_text(
            project_root / "schema.md",
            _DEFAULT_SCHEMA,
        )
        return project

    def save_project(self, project: KnowledgeProject) -> None:
        self._write_json(self.project_path(project.id) / "project.json", project.to_dict())

    def get_project(self, project_id: str) -> KnowledgeProject:
        return KnowledgeProject.from_dict(
            self._read_json(self.project_path(project_id) / "project.json")
        )

    def list_projects(self) -> list[KnowledgeProject]:
        if not self.root.exists():
            return []
        projects: list[KnowledgeProject] = []
        for path in sorted(self.root.glob("*/project.json")):
            try:
                projects.append(KnowledgeProject.from_dict(self._read_json(path)))
            except KnowledgeStoreError:
                continue
        return sorted(projects, key=lambda item: item.updated_at, reverse=True)

    def ir_path(self, project_id: str, source_path: str) -> Path:
        digest = hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:20]
        return self.project_path(project_id) / "knowledge" / "ir" / f"{digest}.json"

    def save_ir(self, ir: KnowledgeIR) -> str:
        path = self.ir_path(ir.project_id, ir.source_path)
        self._write_json(path, ir.to_dict())
        return path.relative_to(self.project_path(ir.project_id)).as_posix()

    def get_ir(self, project_id: str, source_path: str) -> KnowledgeIR:
        return KnowledgeIR.from_dict(
            self._read_json(self.ir_path(project_id, source_path))
        )

    def list_ir(self, project_id: str) -> list[KnowledgeIR]:
        directory = self.project_path(project_id) / "knowledge" / "ir"
        if not directory.exists():
            return []
        values: list[KnowledgeIR] = []
        for path in sorted(directory.glob("*.json")):
            try:
                values.append(KnowledgeIR.from_dict(self._read_json(path)))
            except KnowledgeStoreError:
                continue
        return values

    def review_root(self, project_id: str) -> Path:
        return self.project_path(project_id) / "knowledge" / "reviews"

    def task_path(self, project_id: str) -> Path:
        return self.project_path(project_id) / "knowledge" / "task.json"

    def save_task(self, task: KnowledgeTask) -> str:
        path = self.task_path(task.project_id)
        self._write_json(path, task.to_dict())
        return path.relative_to(self.project_path(task.project_id)).as_posix()

    def get_task(self, project_id: str) -> KnowledgeTask:
        return KnowledgeTask.from_dict(self._read_json(self.task_path(project_id)))

    def save_review(self, review: KnowledgeReview) -> str:
        path = self.review_root(review.project_id) / f"{review.id}.json"
        self._write_json(path, review.to_dict())
        return path.relative_to(self.project_path(review.project_id)).as_posix()

    def list_reviews(self, project_id: str) -> list[KnowledgeReview]:
        if not self.review_root(project_id).exists():
            return []
        values: list[KnowledgeReview] = []
        for path in sorted(self.review_root(project_id).glob("*.json"), reverse=True):
            try:
                values.append(KnowledgeReview.from_dict(self._read_json(path)))
            except KnowledgeStoreError:
                continue
        return values

    def wiki_root(self, project_id: str) -> Path:
        project_root = self.project_path(project_id)
        preferred = project_root / "wiki"
        legacy = project_root / "knowledge" / "wiki"
        # New projects follow the reference wiki shape. Existing projects
        # generated by the first MVP remain readable without migration.
        return preferred if preferred.exists() or not legacy.exists() else legacy

    def raw_root(self, project_id: str) -> Path:
        return self.project_path(project_id) / "raw"

    def raw_path(self, project_id: str, relative_path: str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise KnowledgeStoreError("raw path must be a relative file path")
        root = self.raw_root(project_id).resolve()
        path = (self.raw_root(project_id) / relative).resolve()
        if not path.is_relative_to(root):
            raise KnowledgeStoreError("raw path escapes project")
        return path

    def write_raw(self, project_id: str, relative_path: str, content: bytes) -> Path:
        path = self.raw_path(project_id, relative_path)
        self._write_bytes(path, content)
        return path

    def page_path(self, project_id: str, page_type: str, slug: str) -> Path:
        self.validate_id(slug, "page_slug")
        directory = {
            "entity": "entities",
            "concept": "concepts",
            "source": "sources",
            "query": "queries",
            "comparison": "comparisons",
            "synthesis": "synthesis",
            "overview": "",
        }.get(page_type)
        if directory is None:
            raise KnowledgeStoreError(f"unsupported page type: {page_type}")
        base = self.wiki_root(project_id)
        return base / directory / f"{slug}.md" if directory else base / f"{slug}.md"

    def write_page(self, project_id: str, page: KnowledgePage, content: str) -> Path:
        path = self.page_path(project_id, page.type, page.slug)
        self._write_text(path, content)
        return path

    def read_page(self, project_id: str, page_type: str, slug: str) -> str:
        path = self.page_path(project_id, page_type, slug)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise KnowledgeNotFoundError(f"knowledge page not found: {slug}") from exc
        except OSError as exc:
            raise KnowledgeStoreError(f"cannot read knowledge page {path}: {exc}") from exc


_DEFAULT_SCHEMA = """# Knowledge Wiki Schema

All generated pages use YAML frontmatter with `type`, `title`, `tags`,
`related`, `sources`, `created`, and `updated`. Page types are `entity`,
`concept`, `source`, `query`, `comparison`, `synthesis`, and `overview`.

The Knowledge IR is the source for compilation. Markdown pages are published
views and should not be edited by extraction tools directly.
"""
