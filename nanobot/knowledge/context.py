"""Conditional Runtime Context for active Knowledge tasks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from nanobot.knowledge.store import KnowledgeNotFoundError, KnowledgeStore
from nanobot.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines
from nanobot.security.workspace_access import (
    WORKSPACE_SCOPE_METADATA_KEY,
    resolve_effective_workspace_scope,
)

KNOWLEDGE_CONTEXT_KEY = "knowledge_context"
KNOWLEDGE_REQUESTED_METADATA = "knowledge_requested"
KNOWLEDGE_PROJECT_ID_METADATA = "knowledge_project_id"
KNOWLEDGE_SOURCE_PENDING = "__select_source__"
KNOWLEDGE_CITATIONS_KEY = "knowledge_citations"
KNOWLEDGE_CHANGESET_ID_KEY = "knowledge_changeset_id"
KNOWLEDGE_RETRIEVAL_KEY = "knowledge_retrieval"
MAX_KNOWLEDGE_CONTEXT_CHARS = 4_000
MAX_KNOWLEDGE_CITATIONS = 12
MAX_KNOWLEDGE_QUOTE_CHARS = 1_200


def knowledge_context_raw(metadata: Mapping[str, Any] | None) -> dict[str, str]:
    raw = metadata.get(KNOWLEDGE_CONTEXT_KEY) if metadata else None
    if not isinstance(raw, Mapping):
        return {}
    value = cast(Mapping[str, Any], raw)
    result: dict[str, str] = {}
    for key in ("task_id", "project_id", "source_root", "phase", "selected_project_id", "changeset_id"):
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
    changeset_id: str | None = None,
) -> dict[str, str]:
    current = knowledge_context_raw(metadata)
    values = {
        "project_id": project_id,
        "source_root": source_root,
        "phase": phase,
        "selected_project_id": selected_project_id,
        "task_id": task_id,
        "changeset_id": changeset_id,
    }
    for key, value in values.items():
        if value is not None:
            if value.strip():
                current[key] = value.strip()
            else:
                current.pop(key, None)
    metadata[KNOWLEDGE_CONTEXT_KEY] = current
    return current


def knowledge_citations_raw(metadata: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Return bounded, normalized citations saved by the latest knowledge search.

    Citations are deliberately stored separately from the small string-only
    runtime context.  They are provenance pointers for a subsequent writing
    ChangeSet, not document content to inject into every model request.
    """
    raw = metadata.get(KNOWLEDGE_CITATIONS_KEY) if metadata else None
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for raw_item in cast(list[Any], raw):
        if not isinstance(raw_item, Mapping):
            continue
        item = cast(Mapping[str, Any], raw_item)
        path = item.get("path")
        quote = item.get("quote")
        start_line = item.get("start_line")
        end_line = item.get("end_line")
        if not isinstance(path, str) or not path.strip():
            continue
        if not isinstance(quote, str):
            quote = ""
        if not isinstance(start_line, int) or start_line < 1:
            continue
        if not isinstance(end_line, int) or end_line < start_line:
            continue
        citation: dict[str, Any] = {
            "path": path.strip()[:2_000],
            "start_line": start_line,
            "end_line": end_line,
            "quote": quote[:MAX_KNOWLEDGE_QUOTE_CHARS],
        }
        for key in ("project_id", "source_path", "page_path"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                citation[key] = value.strip()[:2_000]
        result.append(citation)
        if len(result) >= MAX_KNOWLEDGE_CITATIONS:
            break
    return result


def set_knowledge_citations(
    metadata: dict[str, Any],
    citations: Sequence[Mapping[str, Any]],
    *,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Persist only normalized citations for the active knowledge project."""
    normalized: list[dict[str, Any]] = []
    for item in citations:
        candidate = dict(item)
        if project_id and not candidate.get("project_id"):
            candidate["project_id"] = project_id
        normalized.extend(knowledge_citations_raw({KNOWLEDGE_CITATIONS_KEY: [candidate]}))
        if len(normalized) >= MAX_KNOWLEDGE_CITATIONS:
            break
    metadata[KNOWLEDGE_CITATIONS_KEY] = normalized[:MAX_KNOWLEDGE_CITATIONS]
    return normalized[:MAX_KNOWLEDGE_CITATIONS]


def set_knowledge_retrieval(
    metadata: dict[str, Any],
    *,
    query: str,
    mode: str,
    index_algorithm: str | None = None,
    document_count: int = 0,
    relation_count: int = 0,
    seed_nodes: Sequence[str] = (),
) -> dict[str, Any]:
    """Persist only compact retrieval state; snippets stay in citations/results."""
    value: dict[str, Any] = {
        "query": query.strip()[:400],
        "mode": mode.strip()[:20],
        "document_count": max(0, int(document_count)),
        "relation_count": max(0, int(relation_count)),
        "seed_nodes": [str(item)[:160] for item in seed_nodes[:5] if str(item).strip()],
    }
    if isinstance(index_algorithm, str) and index_algorithm.strip():
        value["index_algorithm"] = index_algorithm.strip()[:80]
    metadata[KNOWLEDGE_RETRIEVAL_KEY] = value
    return value


def knowledge_retrieval_raw(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = metadata.get(KNOWLEDGE_RETRIEVAL_KEY) if metadata else None
    if not isinstance(raw, Mapping):
        return {}
    value = cast(Mapping[str, Any], raw)
    result: dict[str, Any] = {}
    for key in ("query", "mode", "index_algorithm"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            result[key] = candidate.strip()[:400]
    for key in ("document_count", "relation_count"):
        candidate = value.get(key)
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            result[key] = max(0, candidate)
    seed_nodes = value.get("seed_nodes")
    if isinstance(seed_nodes, list):
        result["seed_nodes"] = [
            str(item)[:160]
            for item in cast(list[Any], seed_nodes)[:5]
            if str(item).strip()
        ]
    return result


def _request_selected_project(request: Any) -> str | None:
    raw_metadata: object = getattr(request, "metadata", None) if request is not None else None
    metadata: Mapping[str, Any] = (
        cast(Mapping[str, Any], raw_metadata)
        if isinstance(raw_metadata, Mapping)
        else dict[str, Any]()
    )
    value = metadata.get(KNOWLEDGE_PROJECT_ID_METADATA)
    return value.strip() if isinstance(value, str) and value.strip() else None


def knowledge_context_runtime_lines(
    store: KnowledgeStore,
    context: Mapping[str, str],
    *,
    requested_source: str | None = None,
    selected_project_id: str | None = None,
    execution_policy: str | None = None,
    retrieval: Mapping[str, Any] | None = None,
) -> list[str]:
    project_id = context.get("project_id") or selected_project_id
    if not project_id and not requested_source:
        return []
    lines = ["[Knowledge Runtime]"]
    if requested_source == KNOWLEDGE_SOURCE_PENDING:
        lines.extend([
            "Knowledge task requested without a source directory.",
            "Ask the user for the source directory and schema before calling knowledge_scan.",
        ])
    elif requested_source:
        lines.extend([
            "Knowledge task requested by the user.",
            f"Source directory: {requested_source}",
            "Start with knowledge_scan, then extract structured IR before compiling pages.",
        ])
    if project_id:
        lines.append(f"Current Knowledge Project: {project_id}")
        try:
            project = store.get_project(project_id)
            if execution_policy == "auto":
                next_step = (
                    "Next: use knowledge_extract, compile a candidate, validate it, then publish automatically; "
                    "do not ask the user for approval."
                )
            elif execution_policy == "read_only":
                next_step = (
                    "Next: use knowledge_extract, compile a candidate, validate it, then publishing is blocked "
                    "by Read-only policy."
                )
            else:
                next_step = (
                    "Next: use knowledge_extract, compile a candidate, validate it, then wait for human approval "
                    "before publish."
                )
            lines.extend([
                f"Project title: {project.title}",
                f"Phase: {project.phase}",
                f"Scanned sources: {len(project.sources)}",
                f"IR files: {len(project.ir_files)}",
                f"Published pages: {project.page_count}",
                next_step,
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
    if retrieval:
        query = str(retrieval.get("query") or "")
        mode = str(retrieval.get("mode") or "hybrid")
        document_count = retrieval.get("document_count", 0)
        relation_count = retrieval.get("relation_count", 0)
        lines.append(
            f"Last Knowledge retrieval: {mode} query={query[:160]!r}; "
            f"documents={document_count}, relations={relation_count}."
        )
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
        raw_metadata: object = getattr(request, "metadata", None)
        metadata: Mapping[str, Any] = (
            cast(Mapping[str, Any], raw_metadata)
            if isinstance(raw_metadata, Mapping)
            else dict[str, Any]()
        )
        requested_source = metadata.get(KNOWLEDGE_REQUESTED_METADATA)
        if not isinstance(requested_source, str):
            requested_source = session.metadata.get(KNOWLEDGE_REQUESTED_METADATA)
        requested_source = requested_source.strip() if isinstance(requested_source, str) else None
        selected_project_id = _request_selected_project(request)
        if not context and not requested_source and not selected_project_id:
            return None
        store = KnowledgeStore(request.workspace or self.sessions.workspace)
        execution_policy = None
        if WORKSPACE_SCOPE_METADATA_KEY in metadata or WORKSPACE_SCOPE_METADATA_KEY in session.metadata:
            try:
                scope = resolve_effective_workspace_scope(
                    message_metadata=metadata,
                    session_metadata=session.metadata,
                    default_workspace=request.workspace or self.sessions.workspace,
                    default_restrict_to_workspace=False,
                    source_channel=getattr(request, "channel", None),
                )
                execution_policy = scope.execution_policy
            except Exception:
                execution_policy = None
        content = wrap_runtime_context_lines(
            knowledge_context_runtime_lines(
                store,
                context,
                requested_source=requested_source,
                selected_project_id=selected_project_id,
                execution_policy=execution_policy,
                retrieval=knowledge_retrieval_raw(session.metadata),
            )
        )
        if not content:
            return None
        return RuntimeContextBlock(
            source="knowledge_runtime",
            content=content[:MAX_KNOWLEDGE_CONTEXT_CHARS],
        )
