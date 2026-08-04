"""Conditional Runtime Context for active Knowledge tasks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from nanobot.knowledge.store import KnowledgeNotFoundError, KnowledgeStore
from nanobot.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines

KNOWLEDGE_CONTEXT_KEY = "knowledge_context"
KNOWLEDGE_REQUESTED_METADATA = "knowledge_requested"
KNOWLEDGE_PROJECT_ID_METADATA = "knowledge_project_id"
MAX_KNOWLEDGE_CONTEXT_CHARS = 4_000


def knowledge_context_raw(metadata: Mapping[str, Any] | None) -> dict[str, str]:
    raw = metadata.get(KNOWLEDGE_CONTEXT_KEY) if metadata else None
    if not isinstance(raw, Mapping):
        return {}
    value = cast(Mapping[str, Any], raw)
    result: dict[str, str] = {}
    for key in ("task_id", "project_id", "source_root", "phase", "selected_project_id"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            result[key] = candidate.strip()
    return result


def set_knowledge_context(
    metadata: dict[str, Any],
    *,
    project_id: str | None = None,
    source_root: str | None = None,
    phase: str | None = None,
    selected_project_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, str]:
    current = knowledge_context_raw(metadata)
    values = {
        "project_id": project_id,
        "source_root": source_root,
        "phase": phase,
        "selected_project_id": selected_project_id,
        "task_id": task_id,
    }
    for key, value in values.items():
        if value is not None:
            if value.strip():
                current[key] = value.strip()
            else:
                current.pop(key, None)
    metadata[KNOWLEDGE_CONTEXT_KEY] = current
    return current


def _request_selected_project(request: Any) -> str | None:
    metadata = request.metadata if request is not None else {}
    value = metadata.get(KNOWLEDGE_PROJECT_ID_METADATA)
    return value.strip() if isinstance(value, str) and value.strip() else None


def knowledge_context_runtime_lines(
    store: KnowledgeStore,
    context: Mapping[str, str],
    *,
    requested_source: str | None = None,
    selected_project_id: str | None = None,
) -> list[str]:
    project_id = context.get("project_id") or selected_project_id
    if not project_id and not requested_source:
        return []
    lines = ["[Knowledge Runtime]"]
    if requested_source:
        lines.extend([
            "Knowledge task requested by the user.",
            f"Source directory: {requested_source}",
            "Start with knowledge_scan, then extract structured IR before compiling pages.",
        ])
    if project_id:
        lines.append(f"Current Knowledge Project: {project_id}")
        try:
            project = store.get_project(project_id)
            lines.extend([
                f"Project title: {project.title}",
                f"Phase: {project.phase}",
                f"Scanned sources: {len(project.sources)}",
                f"IR files: {len(project.ir_files)}",
                f"Published pages: {project.page_count}",
                "Next: use knowledge_extract, knowledge_compile, knowledge_validate, then knowledge_publish.",
            ])
            try:
                task = store.get_task(project_id)
                lines.extend([
                    f"Knowledge Task: {task.id}",
                    f"Task status: {task.status}",
                    f"Task phase: {task.phase}",
                    f"Pending sources: {len(task.pending_sources)}",
                ])
            except KnowledgeNotFoundError:
                lines.append("Knowledge task state is missing; continue by using the next Knowledge tool.")
        except KnowledgeNotFoundError:
            lines.append("Project pointer is stale; call knowledge_scan to recover it.")
    if selected_project_id and selected_project_id != project_id:
        lines.append(f"Selected Knowledge Project for retrieval: {selected_project_id}")
    lines.append("Preserve source paths and evidence; do not directly edit published wiki Markdown during extraction.")
    lines.append("[/Knowledge Runtime]")
    return lines


class KnowledgeContextProvider:
    """Inject knowledge state only when a task or explicit selection exists."""

    def __init__(self, sessions: Any) -> None:
        self.sessions = sessions

    async def __call__(self, request: Any) -> RuntimeContextBlock | None:
        if not request.session_key:
            return None
        session = self.sessions.get_or_create(request.session_key)
        context = knowledge_context_raw(session.metadata)
        metadata = request.metadata if isinstance(request.metadata, Mapping) else {}
        requested_source = metadata.get(KNOWLEDGE_REQUESTED_METADATA)
        requested_source = requested_source.strip() if isinstance(requested_source, str) else None
        selected_project_id = _request_selected_project(request)
        if not context and not requested_source and not selected_project_id:
            return None
        store = KnowledgeStore(request.workspace or self.sessions.workspace)
        content = wrap_runtime_context_lines(
            knowledge_context_runtime_lines(
                store,
                context,
                requested_source=requested_source,
                selected_project_id=selected_project_id,
            )
        )
        if not content:
            return None
        return RuntimeContextBlock(
            source="knowledge_runtime",
            content=content[:MAX_KNOWLEDGE_CONTEXT_CHARS],
        )
