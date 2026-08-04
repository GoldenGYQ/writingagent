"""Runtime Context Provider for the current Writing Document Runtime."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from nanobot.knowledge.context import (
    KNOWLEDGE_PROJECT_ID_METADATA,
    knowledge_citations_raw,
    knowledge_context_raw,
)
from nanobot.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines
from nanobot.writing.store import WritingNotFoundError, WritingStore

WRITING_CONTEXT_KEY = "writing_context"
MAX_WRITING_CONTEXT_CHARS = 6_000


def writing_context_raw(metadata: Mapping[str, Any] | None) -> dict[str, str]:
    raw = metadata.get(WRITING_CONTEXT_KEY) if metadata else None
    if not isinstance(raw, Mapping):
        return {}
    raw_mapping = cast(Mapping[str, Any], raw)
    result: dict[str, str] = {}
    for key in ("project_id", "document_id", "chapter_id", "revision_id"):
        candidate = raw_mapping.get(key)
        if isinstance(candidate, str) and candidate.strip():
            result[key] = candidate.strip()
    return result


def set_writing_context(
    metadata: dict[str, Any],
    *,
    project_id: str | None = None,
    document_id: str | None = None,
    chapter_id: str | None = None,
    revision_id: str | None = None,
) -> dict[str, str]:
    current = writing_context_raw(metadata)
    for key, value in {
        "project_id": project_id,
        "document_id": document_id,
        "chapter_id": chapter_id,
        "revision_id": revision_id,
    }.items():
        if value is not None:
            if value:
                current[key] = value
            else:
                current.pop(key, None)
    metadata[WRITING_CONTEXT_KEY] = current
    return current


def writing_context_runtime_lines(
    store: WritingStore,
    context: Mapping[str, str],
    *,
    knowledge_project_id: str | None = None,
    knowledge_citation_count: int = 0,
) -> list[str]:
    lines = [
        "[Writing Document Runtime]",
        f"Project: {context.get('project_id', 'none')}",
        f"Document: {context.get('document_id', 'none')}",
        f"Chapter: {context.get('chapter_id', 'none')}",
        f"Revision: {context.get('revision_id', 'initial')}",
    ]
    try:
        project_id = context.get("project_id")
        document_id = context.get("document_id")
        chapter_id = context.get("chapter_id")
        if project_id and document_id:
            project = store.get_project(project_id)
            document = store.get_document(project_id, document_id)
            lines.extend([f"Project title: {project.title}", f"Document title: {document.title}"])
            if chapter_id:
                chapter = next((item for item in document.chapters if item.id == chapter_id), None)
                if chapter:
                    lines.extend([
                        f"Current chapter title: {chapter.title}",
                        f"Chapter status: {chapter.status}",
                        f"Chapter words: {chapter.word_count}",
                    ])
                open_reviews = [
                    issue
                    for issue in store.list_reviews(
                        project_id,
                        document_id=document_id,
                        chapter_id=chapter_id,
                    )
                    if issue.status == "open"
                ]
                if open_reviews:
                    lines.append(f"Pending review issues: {len(open_reviews)}")
                    for issue in open_reviews[:3]:
                        lines.append(f"- {issue.severity}: {issue.description[:240]}")
            if project.goal:
                lines.append(f"Goal: {project.goal[:800]}")
            if project.style:
                lines.append(f"Style: {project.style[:800]}")
    except WritingNotFoundError:
        lines.append("Writing asset lookup: current pointer is stale; use writing_project/document tools to recover.")
    if knowledge_project_id:
        lines.extend([
            f"Selected Knowledge Project: {knowledge_project_id}",
            "Use knowledge_search for evidence-dependent claims before proposing a writing change.",
            "Attach returned citations to writing_changeset.sources; keep quotes and line anchors intact.",
        ])
        if knowledge_citation_count:
            lines.append(f"Recent bounded Knowledge citations available to the next ChangeSet: {knowledge_citation_count}")
    lines.append("Use writing tools for managed documents; do not silently overwrite a newer revision.")
    lines.append("[/Writing Document Runtime]")
    return lines


class WritingContextProvider:
    def __init__(self, store: WritingStore, sessions: Any) -> None:
        self.store = store
        self.sessions = sessions

    async def __call__(self, request: Any) -> RuntimeContextBlock | None:
        if not request.session_key:
            return None
        session = self.sessions.get_or_create(request.session_key)
        context = writing_context_raw(session.metadata)
        if not context:
            return None
        request_metadata = cast(Mapping[str, Any], request.metadata)
        selected_project_id = request_metadata.get(KNOWLEDGE_PROJECT_ID_METADATA)
        selected_project_id = (
            selected_project_id.strip()
            if isinstance(selected_project_id, str) and selected_project_id.strip()
            else None
        )
        if selected_project_id is None:
            knowledge_context = knowledge_context_raw(session.metadata)
            selected_project_id = knowledge_context.get("selected_project_id")
        knowledge_citation_count = len(knowledge_citations_raw(session.metadata))
        content = wrap_runtime_context_lines(
            writing_context_runtime_lines(
                self.store,
                context,
                knowledge_project_id=selected_project_id,
                knowledge_citation_count=knowledge_citation_count,
            )
        )
        return RuntimeContextBlock(
            source="writing_document_runtime",
            content=content[:MAX_WRITING_CONTEXT_CHARS],
        )


def writing_runtime_snapshot(
    store: WritingStore,
    context: Mapping[str, str],
) -> dict[str, Any]:
    """Build a bounded, refreshable WebUI projection of the current asset.

    The snapshot deliberately contains metadata and the current chapter only;
    full chapter text remains the responsibility of the existing file preview
    route.  This keeps REST recovery cheap while preserving the domain
    pointers needed to render Artifact/ChangeSet/Revision state.
    """

    project_id = context.get("project_id")
    document_id = context.get("document_id")
    if not project_id:
        return {"active": False, "context": {}}
    try:
        project = store.get_project(project_id)
        document = store.get_document(project_id, document_id) if document_id else None
        artifact = store.get_artifact(project_id, document.id) if document else None
        chapter = None
        if document and context.get("chapter_id"):
            chapter = next(
                (item for item in document.chapters if item.id == context["chapter_id"]),
                None,
            )
        changesets = store.list_changesets(project_id, document_id=document.id if document else None)
        reviews = store.list_reviews(project_id, document_id=document.id if document else None)
        revisions = store.list_revisions(project_id, document_id=document.id if document else None)
        pending = [item for item in changesets if item.status == "review"][-20:]
        pending_summaries = [
            {
                "id": item.id,
                "document_id": item.document_id,
                "chapter_id": item.chapter_id,
                "base_revision_id": item.base_revision_id,
                "reason": item.reason,
                "impact": item.impact,
                "status": item.status,
                "created_at": item.created_at,
                "changes": [change.to_dict() for change in item.changes],
            }
            for item in pending
        ]
        return {
            "active": True,
            "context": dict(context),
            "project": project.to_dict(),
            "document": document.to_dict() if document else None,
            "artifact": artifact.to_dict() if artifact else None,
            "chapter": chapter.to_dict() if chapter else None,
            "pending_changesets": pending_summaries,
            "open_reviews": [item.to_dict() for item in reviews if item.status == "open"][-20:],
            "revisions": [
                {
                    "id": item.id,
                    "number": item.number,
                    "chapter_id": item.chapter_id,
                    "base_revision_id": item.base_revision_id,
                    "reason": item.reason,
                    "created_at": item.created_at,
                    "status": item.status,
                }
                for item in revisions[-20:]
            ],
        }
    except WritingNotFoundError:
        return {
            "active": False,
            "context": dict(context),
            "error": "writing asset not found; refresh the project/document selection",
        }
