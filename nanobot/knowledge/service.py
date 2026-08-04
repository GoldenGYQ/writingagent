"""Application services behind Knowledge Runtime tools."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from nanobot.knowledge.compiler import compile_project, validate_project
from nanobot.knowledge.ingest import adapter_for_path, supported_source_suffixes
from nanobot.knowledge.models import (
    KnowledgeEntity,
    KnowledgeIR,
    KnowledgePage,
    KnowledgeProject,
    KnowledgeRelation,
    KnowledgeReview,
    KnowledgeSource,
    KnowledgeTask,
    new_id,
)
from nanobot.knowledge.store import KnowledgeNotFoundError, KnowledgeStore, KnowledgeStoreError

_SOURCE_EXTENSIONS = supported_source_suffixes()
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".nanobot", ".venv", "venv", "node_modules",
    "__pycache__", "dist", "build", "tool-results", "wikis",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeService:
    """Coordinate scanning, IR persistence, compilation, validation, and publish."""

    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store

    def _task_for_project(self, project: KnowledgeProject) -> KnowledgeTask:
        """Load or initialize the durable task associated with a project."""
        try:
            task = self.store.get_task(project.id)
        except KnowledgeNotFoundError:
            task = KnowledgeTask(
                id=new_id("task"),
                project_id=project.id,
                source_root=project.source_root,
                schema_name=project.schema_name,
            )
        project.metadata["task_id"] = task.id
        return task

    def _save_task(
        self,
        project: KnowledgeProject,
        task: KnowledgeTask,
        *,
        phase: str | None = None,
        status: str | None = None,
        last_error: str | None = None,
    ) -> None:
        if phase is not None:
            task.phase = phase
        if status is not None:
            task.status = status
        if last_error is not None:
            task.last_error = last_error
        task.updated_at = _now()
        self.store.save_task(task)
        project.metadata["task_id"] = task.id

    def _record_task_error(self, project_id: str, *, phase: str, error: Exception) -> None:
        """Persist a bounded failure state without hiding the original error."""
        try:
            project = self.store.get_project(project_id)
            task = self._task_for_project(project)
            self._save_task(
                project,
                task,
                phase=phase,
                status="needs_changes",
                last_error=str(error)[:2_000],
            )
            project.phase = phase
            project.status = "needs_changes"
            project.updated_at = _now()
            self.store.save_project(project)
        except Exception:
            # The original operation error is more actionable than a secondary
            # persistence error; callers still receive the original exception.
            return

    def resolve_source(self, raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.store.workspace / candidate
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.store.workspace):
            raise KnowledgeStoreError("knowledge source must stay inside the current workspace")
        if not resolved.exists() or not resolved.is_dir():
            raise KnowledgeStoreError(f"knowledge source directory not found: {raw_path}")
        return resolved

    def scan(
        self,
        source_path: str,
        *,
        project_id: str | None = None,
        title: str | None = None,
        schema_name: str = "default",
        max_files: int = 2_000,
    ) -> dict[str, Any]:
        source_root = self.resolve_source(source_path)
        if project_id:
            project = self.store.get_project(project_id)
        else:
            project = KnowledgeProject(
                id=new_id("kb"),
                title=(title or source_root.name or "Knowledge Wiki").strip(),
                source_root=str(source_root),
                schema_name=schema_name.strip() or "default",
            )
            self.store.create_project(project)

        sources: list[KnowledgeSource] = []
        for path in sorted(source_root.rglob("*")):
            if len(sources) >= max(1, min(max_files, 10_000)):
                break
            if not path.is_file():
                continue
            adapter = adapter_for_path(path)
            if adapter is None or path.suffix.lower() not in _SOURCE_EXTENSIONS:
                continue
            if any(part in _SKIP_DIRS for part in path.relative_to(source_root).parts):
                continue
            try:
                stat = path.stat()
                raw = path.read_bytes()
                relative = path.relative_to(source_root).as_posix()
            except OSError:
                continue
            sources.append(
                KnowledgeSource(
                    path=str(path),
                    relative_path=relative,
                    raw_relative_path=Path("sources").joinpath(relative).as_posix(),
                    size=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    sha256=hashlib.sha256(raw).hexdigest(),
                    kind=adapter.kind,
                    metadata=adapter.metadata(),
                )
            )
            self.store.write_raw(project.id, sources[-1].raw_relative_path, raw)

        project.source_root = str(source_root)
        project.sources = sources
        project.phase = "scanned"
        project.status = "active"
        project.updated_at = _now()
        project.metadata["scan_limit"] = max_files
        task = self._task_for_project(project)
        task.source_root = str(source_root)
        task.schema_name = project.schema_name
        task.pending_sources = [source.relative_path for source in sources]
        task.completed_sources = []
        task.last_error = ""
        self._save_task(project, task, phase="scanned", status="active")
        self.store.save_project(project)
        manifest = self.store.project_path(project.id) / "knowledge" / "manifest.json"
        self.store._write_json(
            manifest,
            {
                "version": 1,
                "project_id": project.id,
                "source_root": str(source_root),
                "sources": [source.to_dict() for source in sources],
                "updated_at": project.updated_at,
            },
        )
        return {
            "project": project.to_dict(),
            "task": task.to_dict(),
            "files": len(sources),
            "documents": [source.relative_path for source in sources],
            "manifest": manifest.relative_to(self.store.project_path(project.id)).as_posix(),
            "next": "extract each source with knowledge_extract, then call knowledge_compile",
        }

    def _validate_source_path(self, project: KnowledgeProject, source_path: str) -> str:
        candidate = Path(source_path)
        if candidate.is_absolute():
            resolved = candidate.resolve()
            root = Path(project.source_root).resolve()
            if not resolved.is_relative_to(root):
                raise KnowledgeStoreError("source_path is outside the Knowledge project source root")
            return resolved.relative_to(root).as_posix()
        normalized = Path(source_path).as_posix().lstrip("/")
        if not normalized or normalized.startswith("../") or "/../" in normalized:
            raise KnowledgeStoreError("source_path escapes the Knowledge project source root")
        if project.sources and normalized not in {source.relative_path for source in project.sources}:
            raise KnowledgeStoreError(f"source_path was not returned by knowledge_scan: {source_path}")
        return normalized

    def extract(
        self,
        project_id: str,
        source_path: str,
        *,
        pages: list[Mapping[str, Any]] | None = None,
        relations: list[Mapping[str, Any]] | None = None,
        entities: list[Mapping[str, Any]] | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        project = self.store.get_project(project_id)
        normalized_source = self._validate_source_path(project, source_path)
        page_values = [KnowledgePage.from_dict(item) for item in (pages or [])]
        for page in page_values:
            page.source_path = page.source_path or normalized_source
            page.sources = list(dict.fromkeys([*page.sources, normalized_source]))
        relation_values = [KnowledgeRelation.from_dict(item) for item in (relations or [])]
        for relation in relation_values:
            relation.source_path = relation.source_path or normalized_source
        entity_values = [
            KnowledgeEntity(
                name=str(item.get("name") or "").strip(),
                type=str(item.get("type") or "entity").strip() or "entity",
                description=str(item.get("description") or ""),
                source_path=str(item.get("source_path") or normalized_source),
                metadata=dict(item.get("metadata") or {}),
            )
            for item in (entities or [])
            if str(item.get("name") or "").strip()
        ]
        ir = KnowledgeIR(
            project_id=project_id,
            source_path=normalized_source,
            pages=page_values,
            relations=relation_values,
            entities=entity_values,
            notes=notes.strip(),
        )
        relative_ir = self.store.save_ir(ir)
        project.ir_files = sorted(set([*project.ir_files, relative_ir]))
        project.phase = "extracting"
        project.updated_at = _now()
        task = self._task_for_project(project)
        task.pending_sources = [item for item in task.pending_sources if item != normalized_source]
        if normalized_source not in task.completed_sources:
            task.completed_sources.append(normalized_source)
        self._save_task(project, task, phase="extracting", status="active")
        self.store.save_project(project)
        return {
            "project_id": project_id,
            "source_path": normalized_source,
            "ir_path": relative_ir,
            "pages": len(page_values),
            "entities": len(entity_values),
            "relations": len(relation_values),
            "next": "call knowledge_compile after all selected sources are extracted",
        }

    def compile(self, project_id: str) -> dict[str, Any]:
        try:
            result = compile_project(self.store, project_id)
        except Exception as error:
            self._record_task_error(project_id, phase="compile_failed", error=error)
            raise
        project = self.store.get_project(project_id)
        task = self._task_for_project(project)
        self._save_task(project, task, phase="compiled", status="active")
        self.store.save_project(project)
        return result

    def validate(self, project_id: str) -> dict[str, Any]:
        try:
            result = validate_project(self.store, project_id)
        except Exception as error:
            self._record_task_error(project_id, phase="validation_failed", error=error)
            raise
        project = self.store.get_project(project_id)
        task = self._task_for_project(project)
        self._save_task(
            project,
            task,
            phase="validated" if result["passed"] else "validation_failed",
            status="active" if result["passed"] else "needs_changes",
            last_error="" if result["passed"] else f"{result['issue_count']} validation issue(s)",
        )
        self.store.save_project(project)
        return result

    def review(self, project_id: str) -> dict[str, Any]:
        validation = self.validate(project_id)
        review = KnowledgeReview(
            id=new_id("review"),
            project_id=project_id,
            status="passed" if validation["passed"] else "needs_changes",
            checked_pages=validation["checked_pages"],
            issues=validation["issues"],
        )
        review_path = self.store.save_review(review)
        project = self.store.get_project(project_id)
        task = self._task_for_project(project)
        self._save_task(
            project,
            task,
            phase="validated" if validation["passed"] else "validation_failed",
            status="active" if validation["passed"] else "needs_changes",
            last_error="" if validation["passed"] else f"{validation['issue_count']} validation issue(s)",
        )
        project.metadata["last_review"] = review.to_dict()
        project.updated_at = _now()
        self.store.save_project(project)
        return {
            **validation,
            "review": review.to_dict(),
            "review_path": review_path,
        }

    def publish(self, project_id: str) -> dict[str, Any]:
        project = self.store.get_project(project_id)
        if project.phase not in {"compiled", "validated"}:
            self.compile(project_id)
        validation = self.review(project_id)
        if not validation["passed"]:
            return {"published": False, "validation": validation}
        project = self.store.get_project(project_id)
        task = self._task_for_project(project)
        self._save_task(project, task, phase="published", status="completed", last_error="")
        project.phase = "published"
        project.status = "published"
        project.updated_at = _now()
        self.store.save_project(project)
        return {
            "published": True,
            "project": project.to_dict(),
            "validation": validation,
            "graph_path": "knowledge/graph/graph.json",
        }
