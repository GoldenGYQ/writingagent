"""Application services behind Knowledge Runtime tools."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from nanobot.knowledge.compiler import compile_project, validate_project
from nanobot.knowledge.ingest import adapter_for_path, supported_source_suffixes
from nanobot.knowledge.models import (
    PAGE_DIRECTORIES,
    KnowledgeChangeSet,
    KnowledgeClaim,
    KnowledgeEntity,
    KnowledgeEvidence,
    KnowledgeIR,
    KnowledgePage,
    KnowledgeProject,
    KnowledgeRelation,
    KnowledgeReview,
    KnowledgeReviewIssue,
    KnowledgeSource,
    KnowledgeTask,
    new_id,
)
from nanobot.knowledge.store import KnowledgeNotFoundError, KnowledgeStore, KnowledgeStoreError

_SOURCE_EXTENSIONS = supported_source_suffixes()
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".nanobot", ".venv", "venv", "node_modules",
    "__pycache__", "dist", "build", "tool-results", "wikis",
    "normalized", "normalized-smoke", "extracted", "ocr-output",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _string_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in cast(list[Any], value) if str(item).strip()]


def _mapping_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def _mapping_values(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        cast(Mapping[str, Any], item)
        for item in cast(list[Any], value)
        if isinstance(item, Mapping)
    ]


class KnowledgeService:
    """Coordinate scanning, IR persistence, compilation, validation, and publish."""

    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store

    @staticmethod
    def _assert_writable(project: KnowledgeProject) -> None:
        if project.metadata.get("read_only") is True:
            raise KnowledgeStoreError(
                f"Knowledge project {project.id} is a read-only reference; scan it into a managed project before editing."
            )

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

    def initialize(
        self,
        source_path: str,
        *,
        title: str | None = None,
        schema_name: str = "default",
    ) -> dict[str, Any]:
        """Create a durable project/task boundary without scanning sources.

        ``/knowledge`` uses this lightweight initializer so the task exists
        before the Agent starts calling observable Knowledge tools.  Scanning,
        extraction, compilation, validation, and publishing remain separate
        tool operations; this method intentionally performs no source reads or
        writes other than project metadata and empty artifact directories.
        """
        source_root = self.resolve_source(source_path)
        project = KnowledgeProject(
            id=new_id("kb"),
            title=(title or source_root.name or "Knowledge Wiki").strip(),
            source_root=str(source_root),
            schema_name=schema_name.strip() or "default",
            status="active",
            phase="scanning",
        )
        self.store.create_project(project)
        task = self._task_for_project(project)
        self._save_task(project, task, phase="scanning", status="active")
        project.updated_at = _now()
        self.store.save_project(project)
        return {
            "project": project.to_dict(),
            "task": task.to_dict(),
            "next": "call knowledge_scan with this project_id, then extract structured IR",
        }

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
            self._assert_writable(project)
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
                    sha256="",
                    kind=adapter.kind,
                    metadata=adapter.metadata(),
                    ingestion_adapter=adapter.name,
                    extraction_mode=adapter.extraction_mode,
                    requires_vision=adapter.requires_vision,
                    bounded_read={
                        "mode": adapter.extraction_mode,
                        "instruction": adapter.instruction,
                    },
                )
            )
            try:
                _, digest = self.store.copy_raw(
                    project.id,
                    sources[-1].raw_relative_path,
                    path,
                )
                sources[-1].sha256 = digest
            except OSError:
                sources.pop()
                continue

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
        self.store.write_derived_json(
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
        pages: Sequence[Mapping[str, Any]] | None = None,
        relations: Sequence[Mapping[str, Any]] | None = None,
        entities: Sequence[Mapping[str, Any]] | None = None,
        notes: str = "",
        claims: Sequence[Mapping[str, Any]] | None = None,
        evidence: Sequence[Mapping[str, Any]] | None = None,
        review_hints: Sequence[Mapping[str, Any]] | None = None,
        relation_confidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        project = self.store.get_project(project_id)
        self._assert_writable(project)
        normalized_source = self._validate_source_path(project, source_path)
        page_values = [KnowledgePage.from_dict(item) for item in (pages or [])]
        for page in page_values:
            page.source_path = page.source_path or normalized_source
            page.sources = list(dict.fromkeys([*page.sources, normalized_source]))
            page.evidence = self._normalize_evidence(page.evidence, normalized_source)
        relation_values = [KnowledgeRelation.from_dict(item) for item in (relations or [])]
        for relation in relation_values:
            relation.source_path = relation.source_path or normalized_source
            relation.evidence_refs = self._normalize_evidence(relation.evidence_refs, relation.source_path)
            if relation.evidence and not relation.evidence_refs:
                relation.evidence_refs = [
                    KnowledgeEvidence(source_path=relation.source_path, quote=relation.evidence)
                ]
        entity_values = [
            KnowledgeEntity(
                name=str(item.get("name") or "").strip(),
                type=str(item.get("type") or "entity").strip() or "entity",
                description=str(item.get("description") or ""),
                tags=_string_values(item.get("tags")),
                related=_string_values(item.get("related")),
                source_path=str(item.get("source_path") or normalized_source),
                metadata=_mapping_dict(item.get("metadata")),
                evidence=self._normalize_evidence(
                    [
                        KnowledgeEvidence.from_dict(value)
                        for value in _mapping_values(item.get("evidence"))
                    ],
                    str(item.get("source_path") or normalized_source),
                ),
            )
            for item in (entities or [])
            if str(item.get("name") or "").strip()
        ]
        evidence_values = self._normalize_evidence(
            [KnowledgeEvidence.from_dict(item) for item in (evidence or [])],
            normalized_source,
        )
        claim_values: list[KnowledgeClaim] = []
        for item in claims or []:
            claim = KnowledgeClaim.from_dict(item)
            claim.source_path = claim.source_path or normalized_source
            claim.evidence = self._normalize_evidence(claim.evidence, claim.source_path)
            claim_values.append(claim)
        ir = KnowledgeIR(
            project_id=project_id,
            source_path=normalized_source,
            pages=page_values,
            relations=relation_values,
            entities=entity_values,
            notes=notes.strip(),
            claims=claim_values,
            evidence=evidence_values,
            review_hints=[dict(item) for item in (review_hints or [])],
            relation_confidence={
                str(key): float(value)
                for key, value in (relation_confidence or {}).items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            },
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
            "claims": len(claim_values),
            "evidence": len(evidence_values),
            "next": "call knowledge_compile after all selected sources are extracted",
        }

    @staticmethod
    def _normalize_evidence(
        values: list[KnowledgeEvidence],
        default_source_path: str,
    ) -> list[KnowledgeEvidence]:
        normalized: list[KnowledgeEvidence] = []
        for evidence in values:
            if not evidence.source_path:
                evidence.source_path = default_source_path
            if evidence.end_line is not None and evidence.start_line is None:
                evidence.start_line = evidence.end_line
            if evidence.start_line is not None and evidence.end_line is None:
                evidence.end_line = evidence.start_line
            if evidence.end_line is not None and evidence.start_line is not None:
                if evidence.end_line < evidence.start_line:
                    continue
            evidence.quote = evidence.quote[:20_000]
            normalized.append(evidence)
        return normalized

    def compile(self, project_id: str) -> dict[str, Any]:
        self._assert_writable(self.store.get_project(project_id))
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

    def compile_candidate(self, project_id: str, *, reason: str = "") -> dict[str, Any]:
        """Compile a candidate Wiki/graph without mutating the published views."""
        project = self.store.get_project(project_id)
        self._assert_writable(project)
        changeset = KnowledgeChangeSet(
            id=new_id("kb_changeset"),
            project_id=project_id,
            base_revision_id=project.published_revision_id or project.current_revision_id,
            reason=reason.strip(),
        )
        candidate_root = self.store.candidate_root(project_id, changeset.id)
        candidate_wiki = candidate_root / "wiki"
        candidate_graph = candidate_root / "graph.json"
        result = compile_project(
            self.store,
            project_id,
            output_root=candidate_wiki,
            existing_root=self.store.wiki_root(project_id),
            graph_path=candidate_graph,
        )
        changeset.changes = [{
            "kind": "knowledge_candidate",
            "candidate_path": candidate_root.relative_to(self.store.project_path(project_id)).as_posix(),
            "page_count": len(result.get("pages", [])),
            "graph": result.get("graph", {}),
        }]
        self.store.save_changeset(changeset)
        project = self.store.get_project(project_id)
        project.review_status = "pending"
        project.metadata["active_changeset_id"] = changeset.id
        project.updated_at = _now()
        self.store.save_project(project)
        return {
            **result,
            "candidate": True,
            "changeset": changeset.to_dict(),
            "candidate_path": candidate_root.relative_to(self.store.project_path(project_id)).as_posix(),
            "next": "call knowledge_validate with changeset_id, then request human approval before apply",
        }

    @staticmethod
    def _review_issues(values: list[dict[str, Any]]) -> list[KnowledgeReviewIssue]:
        issues: list[KnowledgeReviewIssue] = []
        for value in values:
            raw_kind = str(value.get("kind") or "suggestion")
            kind = {
                "wikilink": "missing",
                "frontmatter": "suggestion",
                "quality": "suggestion",
                "evidence": "confirmation",
                "graph": "suggestion",
            }.get(raw_kind, raw_kind)
            severity = "high" if kind in {"conflict", "evidence", "duplicate"} else "medium"
            sources = value.get("source_refs") or value.get("sources")
            if not isinstance(sources, list):
                sources = []
            path = str(value.get("path") or "")
            page_refs = value.get("page_refs") or value.get("pages")
            if not isinstance(page_refs, list):
                page_refs = [path] if path else []
            raw_evidence = value.get("evidence")
            evidence = [
                KnowledgeEvidence.from_dict(cast(Mapping[str, Any], item))
                for item in cast(list[Any], raw_evidence)
                if isinstance(item, Mapping)
            ] if isinstance(raw_evidence, list) else []
            source_values = cast(list[Any], sources)
            page_values = cast(list[Any], page_refs)
            issues.append(KnowledgeReviewIssue(
                kind=kind if kind in {"duplicate", "missing", "conflict", "suggestion", "confirmation"} else "suggestion",
                severity=severity,
                title=str(value.get("title") or value.get("message") or kind),
                summary=str(value.get("summary") or value.get("message") or ""),
                source_refs=[str(item) for item in source_values if str(item).strip()],
                page_refs=[str(item) for item in page_values if str(item).strip()],
                evidence=evidence,
                search_keywords=_string_values(value.get("search_keywords")),
                actions=_string_values(value.get("actions")) or ["confirm", "skip", "resolve"],
                resolution=str(value.get("resolution") or ""),
                metadata={"validator": dict(value), "raw_kind": raw_kind},
            ))
        return issues

    def validate_candidate(self, project_id: str, changeset_id: str) -> dict[str, Any]:
        self._assert_writable(self.store.get_project(project_id))
        changeset = self.store.get_changeset(project_id, changeset_id)
        candidate_root = self.store.candidate_root(project_id, changeset.id)
        result = validate_project(
            self.store,
            project_id,
            wiki_root_override=candidate_root / "wiki",
            graph_path_override=candidate_root / "graph.json",
        )
        review_values = list(result.get("issues", []))
        for ir in self.store.list_ir(project_id):
            for hint in ir.review_hints:
                review_values.append({
                    **hint,
                    "source_refs": hint.get("source_refs") or [ir.source_path],
                })
        issues = self._review_issues(review_values)
        review = KnowledgeReview(
            id=new_id("review"),
            project_id=project_id,
            status="passed" if result["passed"] else "needs_changes",
            checked_pages=result["checked_pages"],
            issues=cast(list[KnowledgeReviewIssue | dict[str, Any]], issues),
            changeset_id=changeset.id,
        )
        review_path = self.store.save_review(review)
        changeset.review_id = review.id
        changeset.status = "review" if result["passed"] else "needs_changes"
        changeset.updated_at = _now()
        self.store.save_changeset(changeset)
        project = self.store.get_project(project_id)
        project.review_status = "passed" if result["passed"] else "needs_changes"
        project.metadata["last_review"] = review.to_dict()
        project.updated_at = _now()
        self.store.save_project(project)
        return {
            **result,
            "review": review.to_dict(),
            "review_path": review_path,
            "changeset": changeset.to_dict(),
        }

    def approve_changeset(self, project_id: str, changeset_id: str, *, reviewer: str = "user") -> dict[str, Any]:
        self._assert_writable(self.store.get_project(project_id))
        changeset = self.store.get_changeset(project_id, changeset_id)
        if changeset.status == "applied":
            return {"applied": True, "changeset": changeset.to_dict()}
        review_result = self.validate_candidate(project_id, changeset_id)
        if not review_result["passed"]:
            return {"applied": False, "validation": review_result, "changeset": changeset.to_dict()}
        candidate_root = self.store.candidate_root(project_id, changeset.id)
        candidate_wiki = candidate_root / "wiki"
        target_wiki = self.store.wiki_root(project_id)
        candidate_pages = {
            path.relative_to(candidate_wiki).as_posix()
            for path in candidate_wiki.rglob("*.md")
        }
        # Wiki type directories are compiler-managed projections of the typed
        # IR.  Reconcile them before copying the approved candidate so stale
        # slugs and superseded pages cannot continue polluting retrieval.
        managed_directories = {value for value in PAGE_DIRECTORIES.values() if value}
        for directory in sorted(managed_directories):
            managed_root = target_wiki / directory
            if not managed_root.exists():
                continue
            for path in managed_root.rglob("*.md"):
                relative = path.relative_to(target_wiki).as_posix()
                if relative not in candidate_pages:
                    path.unlink(missing_ok=True)
        for path in sorted(candidate_wiki.rglob("*.md")):
            relative = path.relative_to(candidate_wiki)
            self.store.write_derived_text(target_wiki / relative, path.read_text(encoding="utf-8"))
        candidate_graph = candidate_root / "graph.json"
        target_graph = self.store.project_path(project_id) / "knowledge" / "graph" / "graph.json"
        if candidate_graph.exists():
            self.store.write_derived_text(target_graph, candidate_graph.read_text(encoding="utf-8"))
        revision_id = new_id("kb_revision")
        changeset.status = "applied"
        changeset.approved_at = _now()
        changeset.reviewed_at = _now()
        changeset.reviewed_by = reviewer
        changeset.applied_revision_id = revision_id
        changeset.updated_at = _now()
        self.store.save_changeset(changeset)
        project = self.store.get_project(project_id)
        project.phase = "published"
        project.status = "published"
        project.review_status = "approved"
        project.current_revision_id = revision_id
        project.published_revision_id = revision_id
        project.metadata["active_changeset_id"] = changeset.id
        project.updated_at = _now()
        self.store.save_project(project)
        return {
            "applied": True,
            "revision_id": revision_id,
            "changeset": changeset.to_dict(),
            "project": project.to_dict(),
        }

    def reject_changeset(
        self,
        project_id: str,
        changeset_id: str,
        *,
        feedback: str = "",
        reviewer: str = "user",
    ) -> dict[str, Any]:
        self._assert_writable(self.store.get_project(project_id))
        changeset = self.store.get_changeset(project_id, changeset_id)
        if changeset.status == "applied":
            raise KnowledgeStoreError("an applied Knowledge ChangeSet cannot be rejected")
        changeset.status = "rejected"
        changeset.feedback = feedback.strip()[:4_000]
        changeset.reviewed_at = _now()
        changeset.reviewed_by = reviewer
        changeset.updated_at = _now()
        self.store.save_changeset(changeset)
        project = self.store.get_project(project_id)
        project.review_status = "rejected"
        project.updated_at = _now()
        self.store.save_project(project)
        return {"rejected": True, "changeset": changeset.to_dict()}

    def validate(self, project_id: str) -> dict[str, Any]:
        self._assert_writable(self.store.get_project(project_id))
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
        self._assert_writable(self.store.get_project(project_id))
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

    def publish(self, project_id: str, *, changeset_id: str | None = None) -> dict[str, Any]:
        self._assert_writable(self.store.get_project(project_id))
        if changeset_id:
            return self.approve_changeset(project_id, changeset_id, reviewer="user")
        project = self.store.get_project(project_id)
        active_changeset = project.metadata.get("active_changeset_id")
        if isinstance(active_changeset, str) and active_changeset.strip():
            return {
                "published": False,
                "approval_required": True,
                "changeset_id": active_changeset,
                "message": "Knowledge candidate requires review and explicit ChangeSet approval before publish.",
            }
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
