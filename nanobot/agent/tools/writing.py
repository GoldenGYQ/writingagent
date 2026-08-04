"""Writing Document Runtime tools.

These tools are intentionally narrower than generic file tools.  Managed
chapters are changed through a ChangeSet and become a new Revision only after
an explicit approval action.
"""

# The generic Tool contract intentionally uses ``execute(**kwargs)`` so the
# loader can invoke every tool uniformly.  These tools expose typed keyword
# arguments for readability; the runtime validates the JSON schema before
# calling them.
# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping, cast
from uuid import uuid4

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import ToolContext, current_request_context
from nanobot.agent.tools.schema import (
    ArraySchema,
    IntegerSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.bus.runtime_events import (
    InteractionStateChanged,
    RuntimeEventBus,
    RuntimeEventContext,
    WritingArtifactChanged,
)
from nanobot.knowledge.context import knowledge_citations_raw
from nanobot.security.workspace_access import current_workspace_scope
from nanobot.session.interaction_state import INTERACTION_STATE_KEY, pending_interaction
from nanobot.writing.changeset import ChangeSetService
from nanobot.writing.context import WritingContextProvider, set_writing_context, writing_context_raw
from nanobot.writing.document import DocumentService
from nanobot.writing.models import Document
from nanobot.writing.review import ReviewService
from nanobot.writing.revision import RevisionService
from nanobot.writing.store import WritingNotFoundError, WritingStore, WritingStoreError

_ACTIONS = ("create", "get", "select", "update")
_CHAPTER_ACTIONS = ("create", "read", "select")
_CHANGESET_ACTIONS = ("propose", "get", "approve", "reject")
_REVISION_ACTIONS = ("list", "get", "compare", "restore")
_REVIEW_ACTIONS = ("create", "get", "list", "update")
_SOURCE_SCHEMA = ObjectSchema(
    {
        "path": StringSchema("Source path or URL.", max_length=2000, nullable=True),
        "start_line": IntegerSchema(description="Optional source start line.", nullable=True),
        "end_line": IntegerSchema(description="Optional source end line.", nullable=True),
        "quote": StringSchema("Optional source quote.", max_length=4000, nullable=True),
    },
    additional_properties=True,
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


class _WritingToolMixin:
    def __init__(
        self,
        workspace: str,
        sessions: Any,
        runtime_events: RuntimeEventBus | None = None,
    ) -> None:
        self._workspace = workspace
        self._sessions = sessions
        self._runtime_events = runtime_events
        self._store = WritingStore(workspace)
        self._documents = DocumentService(self._store)

    def _session(self) -> Any | None:
        ctx = current_request_context()
        return self._sessions.get_or_create(ctx.session_key) if ctx and ctx.session_key else None

    def _context(self) -> dict[str, str]:
        session = self._session()
        return writing_context_raw(session.metadata) if session else {}

    def _set_context(self, **values: str | None) -> dict[str, str]:
        session = self._session()
        if session is None:
            return {}
        context = set_writing_context(session.metadata, **values)
        self._sessions.save(session)
        return context

    async def _publish(
        self,
        *,
        project_id: str,
        document_id: str | None = None,
        chapter_id: str | None = None,
        artifact: Mapping[str, Any] | None = None,
        changeset: Mapping[str, Any] | None = None,
        revision: Mapping[str, Any] | None = None,
    ) -> None:
        ctx = current_request_context()
        if not self._runtime_events or not ctx or not ctx.session_key:
            return
        await self._runtime_events.publish(
            WritingArtifactChanged(
                context=RuntimeEventContext(
                    channel=ctx.channel,
                    chat_id=ctx.chat_id,
                    session_key=ctx.session_key,
                    metadata=dict(ctx.metadata or {}),
                ),
                project_id=project_id,
                document_id=document_id,
                chapter_id=chapter_id,
                artifact=dict(artifact or {}),
                changeset=dict(changeset) if changeset else None,
                revision=dict(revision) if revision else None,
            )
        )

    def _project_id(self, provided: str | None = None) -> str | None:
        return provided or self._context().get("project_id")

    def _document(self, project_id: str, document_id: str | None = None) -> Document:
        return self._documents.get_document(
            project_id,
            document_id or self._context().get("document_id", ""),
        )

    def _knowledge_citations_for_project(self, project_id: str) -> list[dict[str, Any]]:
        """Use only citations produced for the currently selected Knowledge project."""
        session = self._session()
        if session is None:
            return []
        request = current_request_context()
        selected_project_id = None
        if request is not None:
            request_metadata = cast(Mapping[str, Any], request.metadata)
            value = request_metadata.get("knowledge_project_id")
            if isinstance(value, str) and value.strip():
                selected_project_id = value.strip()
        if selected_project_id is None:
            knowledge_context = session.metadata.get("knowledge_context")
            if isinstance(knowledge_context, Mapping):
                value = knowledge_context.get("selected_project_id")
                if isinstance(value, str) and value.strip():
                    selected_project_id = value.strip()
        if not selected_project_id:
            return []
        return [
            citation
            for citation in knowledge_citations_raw(session.metadata)
            if citation.get("project_id") == selected_project_id
        ]

    @staticmethod
    def _error(error: Exception) -> ToolResult:
        if isinstance(error, WritingNotFoundError):
            return ToolResult.error(f"Error: {error}")
        if isinstance(error, WritingStoreError):
            return ToolResult.error(f"Error: {error}")
        return ToolResult.error(f"Error: {error}")

    async def _request_changeset_approval(
        self,
        *,
        project_id: str,
        document_id: str,
        chapter_id: str,
        changeset: Any,
    ) -> ToolResult | None:
        """Pause a WebUI turn with the existing structured ChangeApprovalCard.

        The ChangeSet remains durable and in ``review`` state.  Once the user
        chooses ``apply_once`` or ``reject``, the next turn can explicitly
        replay ``writing_changeset`` with the stable ChangeSet id.
        """

        ctx = current_request_context()
        session = self._session()
        if ctx is None or session is None or not ctx.session_key:
            return None
        waiting = pending_interaction(session.metadata)
        if waiting:
            return ToolResult.error(
                f"Error: interaction {waiting.get('id', '')} is already waiting for user input."
            )
        now = datetime.now().astimezone().isoformat()
        request = {
            "id": f"interaction_{uuid4().hex}",
            "pending": True,
            "status": "pending",
            "kind": "change_approval",
            "reason": "writing_changeset_approval",
            "title": "Review proposed writing change",
            "prompt": (
                f"The Agent proposed a change to {changeset.changes[0].path if changeset.changes else 'the chapter'} "
                f"(+{sum(change.added for change in changeset.changes)}/-{sum(change.deleted for change in changeset.changes)})."
            ),
            "fields": [
                {
                    "id": "feedback",
                    "type": "textarea",
                    "label": "Feedback for the next draft",
                    "description": "Optional. This is saved with the rejected ChangeSet and returned to the Agent.",
                    "required": False,
                }
            ],
            "actions": [
                {"id": "apply_once", "label": "Approve ChangeSet", "style": "primary"},
                {"id": "reject", "label": "Reject", "style": "danger"},
            ],
            "change": {
                "tool": "writing_changeset",
                "changeset_id": changeset.id,
                "files": [
                    {
                        "path": change.path,
                        "operation": "update",
                        "added": change.added,
                        "deleted": change.deleted,
                        "diff": {"format": "unified", "text": change.unified_diff},
                    }
                    for change in changeset.changes
                ],
                "added": sum(change.added for change in changeset.changes),
                "deleted": sum(change.deleted for change in changeset.changes),
            },
            "created_at": now,
            "_server": {
                "project_id": project_id,
                "document_id": document_id,
                "chapter_id": chapter_id,
                "changeset_id": changeset.id,
            },
        }
        previous = dict(session.metadata)
        session.metadata[INTERACTION_STATE_KEY] = request
        try:
            self._sessions.save(session)
        except BaseException:
            session.metadata.clear()
            session.metadata.update(previous)
            raise
        if self._runtime_events is not None:
            await self._runtime_events.publish(
                InteractionStateChanged(
                    context=RuntimeEventContext(
                        channel=ctx.channel,
                        chat_id=ctx.chat_id,
                        session_key=ctx.session_key,
                        metadata=dict(ctx.metadata or {}),
                    ),
                    session_metadata=dict(session.metadata),
                )
            )
        return ToolResult.error(
            f"Approval required for ChangeSet {changeset.id}. "
            "After approval call writing_changeset(action='approve', "
            f"changeset_id='{changeset.id}'). If rejected, call writing_changeset(action='reject', "
            f"changeset_id='{changeset.id}', feedback='<user feedback>')."
        )


@tool_parameters(tool_parameters_schema(
    action=StringSchema("Project operation.", enum=_ACTIONS),
    project_id=StringSchema("Existing project id.", max_length=128, nullable=True),
    title=StringSchema("Project title.", max_length=240, nullable=True),
    goal=StringSchema("Writing goal.", max_length=4000, nullable=True),
    style=StringSchema("Style profile.", max_length=4000, nullable=True),
    outline=ArraySchema(
        ObjectSchema(additional_properties=True),
        description="Structured outline nodes.",
        max_items=200,
        nullable=True,
    ),
    required=["action"],
))
class WritingProjectTool(Tool, _WritingToolMixin):
    def __init__(self, workspace: str, sessions: Any, runtime_events: RuntimeEventBus | None = None) -> None:
        _WritingToolMixin.__init__(self, workspace, sessions, runtime_events)
        self._context_provider = WritingContextProvider(self._store, sessions)

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("WritingProjectTool requires sessions")
        return cls(ctx.workspace, ctx.sessions, ctx.runtime_events)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "writing_project"

    @property
    def description(self) -> str:
        return "Create or inspect a durable Writing Project. Use this before managing documents or chapters."

    def runtime_context_provider(self):
        return self._context_provider

    async def execute(
        self,
        action: str,
        project_id: str | None = None,
        title: str | None = None,
        goal: str | None = None,
        style: str | None = None,
        outline: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> str:
        try:
            if action == "create":
                if not title:
                    return ToolResult.error("Error: writing_project create requires title.")
                project = self._documents.create_project(
                    title,
                    goal=goal or "",
                    style=style or "",
                    outline=outline,
                )
                self._set_context(project_id=project.id, document_id="", chapter_id="", revision_id="")
                await self._publish(project_id=project.id, artifact=project.to_dict())
                return _json({"project": project.to_dict(), "next": "create a document with writing_document"})
            if not project_id:
                project_id = self._project_id()
            if not project_id:
                return ToolResult.error("Error: provide project_id or create a project first.")
            project = self._documents.get_project(project_id)
            if action == "update":
                if title is not None:
                    project.title = title.strip()
                if goal is not None:
                    project.goal = goal.strip()
                if style is not None:
                    project.style = style.strip()
                if outline is not None:
                    project.outline = list(outline)
                project.updated_at = datetime.now().astimezone().isoformat()
                self._store.save_project(project)
            self._set_context(project_id=project.id)
            await self._publish(project_id=project.id, artifact=project.to_dict())
            return _json({"project": project.to_dict()})
        except Exception as error:
            return self._error(error)


@tool_parameters(tool_parameters_schema(
    action=StringSchema("Document operation.", enum=_ACTIONS),
    project_id=StringSchema("Project id.", max_length=128, nullable=True),
    document_id=StringSchema("Existing document id.", max_length=128, nullable=True),
    title=StringSchema("Document title.", max_length=240, nullable=True),
    status=StringSchema("Document lifecycle status.", max_length=40, nullable=True),
    required=["action"],
))
class WritingDocumentTool(Tool, _WritingToolMixin):
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("WritingDocumentTool requires sessions")
        return cls(ctx.workspace, ctx.sessions, ctx.runtime_events)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "writing_document"

    @property
    def description(self) -> str:
        return "Create or inspect a managed Document composed of semantic chapters."

    async def execute(
        self,
        action: str,
        project_id: str | None = None,
        document_id: str | None = None,
        title: str | None = None,
        status: str | None = None,
        **_: Any,
    ) -> str:
        try:
            project_id = self._project_id(project_id)
            if not project_id:
                return ToolResult.error("Error: provide project_id or create a writing project first.")
            if action == "create":
                if not title:
                    return ToolResult.error("Error: writing_document create requires title.")
                document = self._documents.create_document(project_id, title, document_id=document_id)
            elif action == "update":
                if not document_id:
                    return ToolResult.error("Error: writing_document update requires document_id.")
                document = self._documents.update_document(
                    project_id,
                    document_id,
                    title=title,
                    status=status,
                )
            else:
                document = self._document(project_id, document_id)
            self._set_context(project_id=project_id, document_id=document.id, chapter_id="", revision_id="")
            await self._publish(project_id=project_id, document_id=document.id, artifact=document.to_dict())
            return _json({"document": document.to_dict()})
        except Exception as error:
            return self._error(error)


_CHAPTER_SCHEMA = tool_parameters_schema(
    action=StringSchema("Chapter operation.", enum=_CHAPTER_ACTIONS),
    project_id=StringSchema("Project id.", max_length=128, nullable=True),
    document_id=StringSchema("Document id.", max_length=128, nullable=True),
    chapter_id=StringSchema("Chapter id.", max_length=128, nullable=True),
    title=StringSchema("Chapter title.", max_length=240, nullable=True),
    content=StringSchema("Initial chapter Markdown. Do not use for revisions.", max_length=200_000, nullable=True),
    order=IntegerSchema(description="Chapter order.", minimum=1, nullable=True),
    summary=StringSchema("Chapter summary.", max_length=4000, nullable=True),
    required=["action"],
)


@tool_parameters(_CHAPTER_SCHEMA)
class WritingChapterTool(Tool, _WritingToolMixin):
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("WritingChapterTool requires sessions")
        return cls(ctx.workspace, ctx.sessions, ctx.runtime_events)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "writing_chapter"

    @property
    def description(self) -> str:
        return "Create, read or select a semantic chapter. Modify existing content through writing_changeset."

    async def execute(
        self,
        action: str,
        project_id: str | None = None,
        document_id: str | None = None,
        chapter_id: str | None = None,
        title: str | None = None,
        content: str | None = None,
        order: int | None = None,
        summary: str | None = None,
        **_: Any,
    ) -> str:
        try:
            project_id = self._project_id(project_id)
            if not project_id:
                return ToolResult.error("Error: writing_chapter requires a project context.")
            document = self._document(project_id, document_id)
            if action == "create":
                if not title:
                    return ToolResult.error("Error: writing_chapter create requires title.")
                chapter = self._documents.create_chapter(
                    document,
                    title,
                    content=content or "",
                    chapter_id=chapter_id,
                    order=order,
                    summary=summary or "",
                )
            else:
                if not chapter_id:
                    chapter_id = self._context().get("chapter_id")
                if not chapter_id:
                    return ToolResult.error("Error: provide chapter_id or select a chapter first.")
                chapter, chapter_content = self._documents.read_chapter(document, chapter_id)
                self._set_context(project_id=project_id, document_id=document.id, chapter_id=chapter.id, revision_id=chapter.current_revision_id or "")
                return _json({"chapter": chapter.to_dict(), "content": chapter_content})
            self._set_context(project_id=project_id, document_id=document.id, chapter_id=chapter.id, revision_id=chapter.current_revision_id or "")
            await self._publish(project_id=project_id, document_id=document.id, chapter_id=chapter.id, artifact=chapter.to_dict())
            return _json({"chapter": chapter.to_dict()})
        except Exception as error:
            return self._error(error)


@tool_parameters(tool_parameters_schema(
    action=StringSchema("ChangeSet operation.", enum=_CHANGESET_ACTIONS),
    project_id=StringSchema("Project id.", max_length=128, nullable=True),
    document_id=StringSchema("Document id.", max_length=128, nullable=True),
    chapter_id=StringSchema("Chapter id.", max_length=128, nullable=True),
    changeset_id=StringSchema("Existing ChangeSet id.", max_length=128, nullable=True),
    base_revision_id=StringSchema("Revision the proposal was based on.", max_length=128, nullable=True),
    proposed_content=StringSchema("Complete proposed Markdown for the chapter.", max_length=200_000, nullable=True),
    reason=StringSchema("Why the change is needed.", max_length=4000, nullable=True),
    impact=StringSchema("Sections or behavior affected.", max_length=2000, nullable=True),
    sources=ArraySchema(_SOURCE_SCHEMA, description="Source references supporting the change.", max_items=30, nullable=True),
    author=StringSchema("Revision author.", max_length=120, nullable=True),
    feedback=StringSchema("User feedback when rejecting a ChangeSet.", max_length=8000, nullable=True),
    required=["action"],
))
class WritingChangeSetTool(Tool, _WritingToolMixin):
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("WritingChangeSetTool requires sessions")
        return cls(ctx.workspace, ctx.sessions, ctx.runtime_events)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "writing_changeset"

    @property
    def description(self) -> str:
        return (
            "Propose, inspect, approve or reject a version-safe semantic document change. "
            "The workspace execution policy controls submission: auto applies immediately; "
            "ask creates a Diff/Preview approval request; reject with feedback to revise the draft. "
            "When sources are omitted, citations from the latest selected Knowledge search for this "
            "project are attached automatically; pass sources explicitly to override this behavior."
        )

    @property
    def exclusive(self) -> bool:
        return True

    async def execute(
        self,
        action: str,
        project_id: str | None = None,
        document_id: str | None = None,
        chapter_id: str | None = None,
        changeset_id: str | None = None,
        base_revision_id: str | None = None,
        proposed_content: str | None = None,
        reason: str | None = None,
        impact: str | None = None,
        sources: list[dict[str, Any]] | None = None,
        author: str | None = None,
        feedback: str | None = None,
        **_: Any,
    ) -> str:
        try:
            project_id = self._project_id(project_id)
            if not project_id:
                return ToolResult.error("Error: writing_changeset requires a project context.")
            service = ChangeSetService(self._store)
            if action == "get":
                if not changeset_id:
                    return ToolResult.error("Error: get requires changeset_id.")
                return _json({"changeset": service.get(project_id, changeset_id).to_dict()})
            if action == "reject":
                if not changeset_id:
                    return ToolResult.error("Error: reject requires changeset_id.")
                changeset = service.reject(
                    project_id,
                    changeset_id,
                    feedback=feedback or "",
                    reviewer=author or "user",
                )
                await self._publish(project_id=project_id, document_id=changeset.document_id, chapter_id=changeset.chapter_id, changeset=changeset.to_dict())
                return _json({
                    "changeset": changeset.to_dict(),
                    "next": "revise and propose a new ChangeSet using the saved feedback",
                })
            document = self._document(project_id, document_id)
            if action == "propose":
                if not chapter_id or proposed_content is None or not reason:
                    return ToolResult.error("Error: propose requires chapter_id, proposed_content and reason.")
                chapter = self._documents.get_chapter(document, chapter_id)
                auto_knowledge_citations = sources is None
                effective_sources = (
                    self._knowledge_citations_for_project(project_id)
                    if auto_knowledge_citations
                    else sources or []
                )
                changeset = service.propose(
                    document,
                    chapter,
                    proposed_content,
                    reason=reason,
                    impact=impact or "",
                    sources=effective_sources,
                    base_revision_id=base_revision_id,
                )
                self._set_context(project_id=project_id, document_id=document.id, chapter_id=chapter.id, revision_id=chapter.current_revision_id or "")
                await self._publish(project_id=project_id, document_id=document.id, chapter_id=chapter.id, changeset=changeset.to_dict())
                scope = current_workspace_scope()
                execution_policy = scope.execution_policy if scope is not None else "ask"
                if execution_policy == "auto":
                    changeset = service.approve(document, changeset, author=author or "agent")
                    revision = (
                        self._store.get_revision(project_id, changeset.applied_revision_id)
                        if changeset.applied_revision_id
                        else None
                    )
                    self._set_context(
                        project_id=project_id,
                        document_id=document.id,
                        chapter_id=changeset.chapter_id,
                        revision_id=revision.id if revision else "",
                    )
                    await self._publish(
                        project_id=project_id,
                        document_id=document.id,
                        chapter_id=changeset.chapter_id,
                        changeset=changeset.to_dict(),
                        revision=revision.to_dict() if revision else None,
                    )
                    return _json({
                        "changeset": changeset.to_dict(),
                        "revision": revision.to_dict() if revision else None,
                        "execution_policy": execution_policy,
                        "knowledge_citations_used": len(effective_sources) if auto_knowledge_citations else 0,
                        "next": "continue the task",
                    })
                if execution_policy == "read_only":
                    return _json({
                        "changeset": changeset.to_dict(),
                        "execution_policy": execution_policy,
                        "knowledge_citations_used": len(effective_sources) if auto_knowledge_citations else 0,
                        "next": "the proposal is read-only and was not applied",
                    })
                approval = await self._request_changeset_approval(
                    project_id=project_id,
                    document_id=document.id,
                    chapter_id=chapter.id,
                    changeset=changeset,
                )
                if approval is not None:
                    return approval
                return _json({
                    "changeset": changeset.to_dict(),
                    "knowledge_citations_used": len(effective_sources) if auto_knowledge_citations else 0,
                    "next": "show the ChangeSet and wait for approval",
                })
            if not changeset_id:
                return ToolResult.error("Error: approve requires changeset_id.")
            changeset = service.get(project_id, changeset_id)
            changeset = service.approve(document, changeset, author=author or "agent")
            revision = self._store.get_revision(project_id, changeset.applied_revision_id) if changeset.applied_revision_id else None
            self._set_context(project_id=project_id, document_id=document.id, chapter_id=changeset.chapter_id, revision_id=revision.id if revision else "")
            await self._publish(
                project_id=project_id,
                document_id=document.id,
                chapter_id=changeset.chapter_id,
                changeset=changeset.to_dict(),
                revision=revision.to_dict() if revision else None,
            )
            return _json({"changeset": changeset.to_dict(), "revision": revision.to_dict() if revision else None})
        except Exception as error:
            return self._error(error)


@tool_parameters(tool_parameters_schema(
    action=StringSchema("Revision operation.", enum=_REVISION_ACTIONS),
    project_id=StringSchema("Project id.", max_length=128, nullable=True),
    document_id=StringSchema("Document id.", max_length=128, nullable=True),
    chapter_id=StringSchema("Chapter id.", max_length=128, nullable=True),
    revision_id=StringSchema("Revision id.", max_length=128, nullable=True),
    from_revision_id=StringSchema("Comparison base revision.", max_length=128, nullable=True),
    to_revision_id=StringSchema("Comparison target revision.", max_length=128, nullable=True),
    author=StringSchema("Restore author.", max_length=120, nullable=True),
    required=["action"],
))
class WritingRevisionTool(Tool, _WritingToolMixin):
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("WritingRevisionTool requires sessions")
        return cls(ctx.workspace, ctx.sessions, ctx.runtime_events)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "writing_revision"

    @property
    def description(self) -> str:
        return "List, inspect, compare or restore immutable chapter Revisions."

    @property
    def exclusive(self) -> bool:
        return True

    async def execute(
        self,
        action: str,
        project_id: str | None = None,
        document_id: str | None = None,
        chapter_id: str | None = None,
        revision_id: str | None = None,
        from_revision_id: str | None = None,
        to_revision_id: str | None = None,
        author: str | None = None,
        **_: Any,
    ) -> str:
        try:
            project_id = self._project_id(project_id)
            if not project_id:
                return ToolResult.error("Error: writing_revision requires a project context.")
            revisions = RevisionService(self._store)
            if action == "list":
                values = revisions.list(project_id, document_id=document_id, chapter_id=chapter_id)
                return _json({"revisions": [item.to_dict() for item in values]})
            if not revision_id and action != "compare":
                return ToolResult.error("Error: this revision action requires revision_id.")
            if action == "get":
                return _json({"revision": self._store.get_revision(project_id, cast(str, revision_id)).to_dict()})
            if action == "compare":
                if not from_revision_id or not to_revision_id:
                    return ToolResult.error("Error: compare requires from_revision_id and to_revision_id.")
                comparison = revisions.compare(project_id, from_revision_id, to_revision_id)
                return _json({
                    "from": comparison.from_revision,
                    "to": comparison.to_revision,
                    "added": comparison.added,
                    "deleted": comparison.deleted,
                    "diff": comparison.unified_diff,
                })
            document = self._document(project_id, document_id)
            chapter_id = chapter_id or self._context().get("chapter_id")
            if not chapter_id:
                return ToolResult.error("Error: restore requires chapter_id or a selected chapter.")
            chapter = self._documents.get_chapter(document, chapter_id)
            revision = revisions.restore(document, chapter, cast(str, revision_id), author=author or "user")
            self._set_context(project_id=project_id, document_id=document.id, chapter_id=chapter.id, revision_id=revision.id)
            await self._publish(project_id=project_id, document_id=document.id, chapter_id=chapter.id, revision=revision.to_dict())
            return _json({"revision": revision.to_dict()})
        except Exception as error:
            return self._error(error)


_REVIEW_SCHEMA = ObjectSchema(
    {
        "action": StringSchema("Review operation.", enum=_REVIEW_ACTIONS),
        "project_id": StringSchema("Project id.", max_length=128, nullable=True),
        "document_id": StringSchema("Document id.", max_length=128, nullable=True),
        "chapter_id": StringSchema("Chapter id.", max_length=128, nullable=True),
        "review_id": StringSchema("Review issue id.", max_length=128, nullable=True),
        "revision_id": StringSchema("Revision id.", max_length=128, nullable=True),
        "kind": StringSchema("Issue type.", max_length=80, nullable=True),
        "severity": StringSchema("Issue severity.", max_length=40, nullable=True),
        "description": StringSchema("Review issue description.", max_length=4000, nullable=True),
        "suggestion": StringSchema("Suggested correction.", max_length=4000, nullable=True),
        "start_line": IntegerSchema(description="Issue start line.", minimum=1, nullable=True),
        "end_line": IntegerSchema(description="Issue end line.", minimum=1, nullable=True),
        "status": StringSchema(
            "Review status.", enum=("open", "accepted", "dismissed", "fixed"), nullable=True
        ),
        "sources": ArraySchema(
            StringSchema(description="Source id or path."), max_items=30, nullable=True
        ),
    },
    required=["action"],
)


@tool_parameters(_REVIEW_SCHEMA.to_json_schema())
class WritingReviewTool(Tool, _WritingToolMixin):
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("WritingReviewTool requires sessions")
        return cls(ctx.workspace, ctx.sessions, ctx.runtime_events)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "writing_review"

    @property
    def description(self) -> str:
        return "Create and maintain structured review issues anchored to a document chapter."

    async def execute(
        self,
        action: str,
        project_id: str | None = None,
        document_id: str | None = None,
        chapter_id: str | None = None,
        review_id: str | None = None,
        revision_id: str | None = None,
        kind: str | None = None,
        severity: str | None = None,
        description: str | None = None,
        suggestion: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        status: str | None = None,
        sources: list[str] | None = None,
        **_: Any,
    ) -> str:
        try:
            project_id = self._project_id(project_id)
            if not project_id:
                return ToolResult.error("Error: writing_review requires a project context.")
            service = ReviewService(self._store)
            if action == "list":
                values = service.list(project_id, document_id=document_id, chapter_id=chapter_id)
                return _json({"reviews": [item.to_dict() for item in values]})
            if action == "get":
                if not review_id:
                    return ToolResult.error("Error: get requires review_id.")
                return _json({"review": self._store.get_review(project_id, review_id).to_dict()})
            if action == "update":
                if not review_id or not status:
                    return ToolResult.error("Error: update requires review_id and status.")
                issue = service.update_status(project_id, review_id, status)
            else:
                if not document_id or not chapter_id or not description:
                    return ToolResult.error("Error: create requires document_id, chapter_id and description.")
                issue = service.create_issue(
                    project_id,
                    document_id=document_id,
                    chapter_id=chapter_id,
                    revision_id=revision_id,
                    kind=kind or "general",
                    severity=severity or "medium",
                    description=description,
                    suggestion=suggestion or "",
                    start_line=start_line,
                    end_line=end_line,
                    sources=sources,
                )
            await self._publish(project_id=project_id, document_id=issue.document_id, chapter_id=issue.chapter_id, artifact=issue.to_dict())
            return _json({"review": issue.to_dict()})
        except Exception as error:
            return self._error(error)
