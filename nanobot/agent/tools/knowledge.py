"""Structured Knowledge Runtime tools.

Extraction produces JSON-like Knowledge IR.  Only the compiler writes wiki
Markdown, which keeps the Agent Runtime's tool boundary explicit and makes
validation/rebuilds deterministic.
"""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import ToolContext, current_request_context
from nanobot.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    IntegerSchema,
    NumberSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.bus.runtime_events import InteractionStateChanged, RuntimeEventBus, RuntimeEventContext
from nanobot.knowledge.context import (
    KNOWLEDGE_PROJECT_ID_METADATA,
    KNOWLEDGE_REQUESTED_METADATA,
    KnowledgeContextProvider,
    knowledge_context_raw,
    set_knowledge_citations,
    set_knowledge_context,
    set_knowledge_retrieval,
)
from nanobot.knowledge.models import PAGE_TYPES
from nanobot.knowledge.normalization import normalize_source
from nanobot.knowledge.preferences import (
    allow_query_rewrite,
    resolve_research_options,
    resolve_search_options,
)
from nanobot.knowledge.retriever import KnowledgeRetriever
from nanobot.knowledge.service import KnowledgeService
from nanobot.knowledge.store import KnowledgeNotFoundError, KnowledgeStore, KnowledgeStoreError
from nanobot.security.workspace_access import (
    WORKSPACE_SCOPE_METADATA_KEY,
    WorkspaceScope,
    current_workspace_scope,
    workspace_scope_from_metadata,
)
from nanobot.session.interaction_state import (
    INTERACTION_STATE_KEY,
    parse_interaction,
    pending_interaction,
)
from nanobot.session.manager import SessionManager


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    raw = cast(Mapping[object, object], value)
    return {str(key): item for key, item in raw.items()}


def _items(value: object) -> list[Any]:
    return list(cast(list[Any], value)) if isinstance(value, list) else []


def _mapping_items(value: object) -> list[dict[str, Any]]:
    return [_mapping(cast(object, item)) for item in _items(value) if isinstance(item, Mapping)]


def _project_summary(project: Any) -> dict[str, Any]:
    project_data = _mapping(project)
    if not project_data:
        return {}
    summary: dict[str, Any] = {
        key: value
        for key, value in project_data.items()
        if key not in {"sources", "metadata"}
    }
    summary["source_count"] = len(_items(project_data.get("sources")))
    return summary


def _bounded_scan_result(result: dict[str, Any]) -> dict[str, Any]:
    raw_documents = result.get("documents")
    documents = _items(raw_documents)
    if not isinstance(raw_documents, list):
        return result
    result = dict(result)
    result["project"] = _project_summary(result.get("project"))
    result["documents"] = documents[:200]
    result["documents_truncated"] = len(documents) > len(result["documents"])
    return result


def _source_citation(
    store: KnowledgeStore,
    project: Any,
    source_path: str,
    needle: str,
) -> dict[str, Any] | None:
    """Return a line-anchored citation from the mirrored raw source when possible."""
    source = next(
        (item for item in project.sources if item.relative_path == source_path),
        None,
    )
    if source is None:
        return None
    try:
        if source.raw_relative_path:
            path = store.raw_path(project.id, source.raw_relative_path)
        else:
            path = Path(project.source_root) / source.relative_path
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    hit = next((index for index, line in enumerate(lines) if needle in line.casefold()), None)
    if hit is None:
        return None
    start = max(0, hit - 2)
    end = min(len(lines), hit + 5)
    quote = "\n".join(lines[start:end])[:800]
    return {
        "path": path.relative_to(store.workspace).as_posix(),
        "start_line": start + 1,
        "end_line": end,
        "quote": quote,
        "project_id": project.id,
        "source_path": source.relative_path,
    }


def _evidence_citation(
    store: KnowledgeStore,
    project: Any,
    evidence: Any,
) -> dict[str, Any] | None:
    """Turn a persisted evidence object into a bounded writing citation."""
    if isinstance(evidence, Mapping):
        evidence_data = _mapping(cast(object, evidence))
        source_path: object = evidence_data.get("source_path", "")
        quote: object = evidence_data.get("quote", "")
        start_line: object = evidence_data.get("start_line")
        end_line: object = evidence_data.get("end_line")
    else:
        source_path = getattr(evidence, "source_path", "")
        quote = getattr(evidence, "quote", "")
        start_line = getattr(evidence, "start_line", None)
        end_line = getattr(evidence, "end_line", None)
    if not isinstance(source_path, str) or not source_path.strip() or not isinstance(start_line, int):
        return None
    if not isinstance(end_line, int) or end_line < start_line:
        end_line = start_line
    source = next((item for item in project.sources if item.relative_path == source_path), None)
    if source is None or not source.raw_relative_path:
        return None
    path = store.raw_path(project.id, source.raw_relative_path)
    return {
        "path": path.relative_to(store.workspace).as_posix(),
        "start_line": start_line,
        "end_line": end_line,
        "quote": str(quote or "")[:1200],
        "project_id": project.id,
        "source_path": source_path,
    }


def _store(default_workspace: str) -> KnowledgeStore:
    request = current_request_context()
    return KnowledgeStore(request.workspace or default_workspace if request else default_workspace)


class _KnowledgeToolMixin:
    def __init__(
        self,
        workspace: str,
        sessions: SessionManager,
        runtime_events: RuntimeEventBus | None = None,
    ) -> None:
        self._workspace = workspace
        self._sessions = sessions
        self._runtime_events = runtime_events

    def _session(self) -> Any | None:
        request = current_request_context()
        if request is None or not request.session_key:
            return None
        return self._sessions.get_or_create(request.session_key)

    def _context(self) -> dict[str, str]:
        session = self._session()
        return knowledge_context_raw(session.metadata) if session else {}

    def _effective_workspace_scope(self) -> WorkspaceScope | None:
        """Resolve the policy for this tool, including durable session fallback.

        The normal AgentLoop path binds a scope with a ContextVar.  A tool can
        also run while an interaction is being resumed (or in an isolated
        tool/test invocation), where that binding is absent.  The WebUI scope
        persisted in the request/session is still authoritative in that case;
        falling back to ``ask`` here used to make Knowledge publish ignore
        Auto Apply and emit a second approval form.
        """
        scope = current_workspace_scope()
        if scope is not None:
            return scope
        request = current_request_context()
        session = self._session()
        if request is None and session is None:
            return None
        request_metadata = request.metadata if request is not None else None
        session_metadata = session.metadata if session is not None else None
        metadata_source: dict[str, Any] | None = None
        request_data = _mapping(request_metadata)
        session_data = _mapping(session_metadata)
        if WORKSPACE_SCOPE_METADATA_KEY in request_data:
            metadata_source = request_data
        elif WORKSPACE_SCOPE_METADATA_KEY in session_data:
            metadata_source = session_data
        if metadata_source is None:
            return None
        try:
            return workspace_scope_from_metadata(
                metadata_source,
                default_workspace=(request.workspace if request and request.workspace else self._workspace),
                default_restrict_to_workspace=False,
                source_channel=request.channel if request is not None else "websocket",
            )
        except Exception:
            # Preserve the safe historical behavior for malformed/legacy
            # metadata.  A caller can still explicitly bind a scope.
            return None

    def _effective_execution_policy(self) -> str:
        scope = self._effective_workspace_scope()
        return scope.execution_policy if scope is not None else "ask"

    def _set_context(self, **values: str | None) -> dict[str, str]:
        session = self._session()
        if session is None:
            return {}
        context = set_knowledge_context(session.metadata, **values)
        self._sessions.save(session)
        return context

    def _set_citations(
        self,
        citations: list[dict[str, Any]],
        *,
        project_id: str,
    ) -> list[dict[str, Any]]:
        session = self._session()
        if session is None:
            return []
        normalized = set_knowledge_citations(
            session.metadata,
            citations,
            project_id=project_id,
        )
        self._sessions.save(session)
        return normalized

    def _set_retrieval(self, result: Any) -> dict[str, Any]:
        session = self._session()
        if session is None:
            return {}
        result_data = _mapping(result)
        retrieval = _mapping(result_data.get("retrieval"))
        if not retrieval:
            return {}
        documents = _items(result_data.get("documents"))
        relations = _items(result_data.get("relations"))
        seed_nodes = [str(item) for item in _items(retrieval.get("seed_nodes"))]
        index_algorithm = retrieval.get("index_algorithm")
        value = set_knowledge_retrieval(
            session.metadata,
            query=str(result_data.get("query") or ""),
            mode=str(result_data.get("mode") or "hybrid"),
            index_algorithm=str(index_algorithm) if index_algorithm is not None else None,
            document_count=len(documents),
            relation_count=len(relations),
            seed_nodes=seed_nodes,
        )
        self._sessions.save(session)
        return value

    async def _request_knowledge_approval(
        self,
        *,
        project_id: str,
        changeset_id: str,
    ) -> ToolResult | None:
        """Create or consume the durable HITL gate for a candidate ChangeSet."""
        # Knowledge publishing must use the same execution policy as file and
        # Writing ChangeSet mutations.  Previously this helper unconditionally
        # created an interaction request, so the WebUI's Auto Apply setting was
        # ignored specifically for knowledge candidates.
        execution_policy = self._effective_execution_policy()
        if execution_policy == "auto":
            return None
        if execution_policy == "read_only":
            return ToolResult.error(
                "Error: Knowledge publish blocked by Read-only execution policy. "
                "Change the execution policy before retrying."
            )

        ctx = current_request_context()
        session = self._session()
        if ctx is None or session is None or not ctx.session_key:
            return ToolResult.error(
                "Error: Knowledge candidate approval requires an active durable session."
            )
        existing = parse_interaction(session.metadata.get(INTERACTION_STATE_KEY))
        if existing:
            server = _mapping(existing.get("_server"))
            same = (
                server.get("kind") == "knowledge_changeset_approval"
                and server.get("project_id") == project_id
                and server.get("changeset_id") == changeset_id
            )
            response = _mapping(existing.get("response"))
            action = response.get("action")
            if same and existing.get("status") == "resolved" and action == "apply_once":
                server["consumed_at"] = datetime.now().astimezone().isoformat()
                existing["status"] = "consumed"
                session.metadata[INTERACTION_STATE_KEY] = existing
                self._sessions.save(session)
                return None
            if same and existing.get("status") == "resolved":
                return ToolResult.error(
                    f"Error: Knowledge ChangeSet {changeset_id} was rejected by the user."
                )
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
            "reason": "knowledge_changeset_approval",
            "title": "Review Knowledge ChangeSet",
            "prompt": (
                "The Agent compiled a candidate Knowledge Wiki and graph. Review the candidate and "
                "approve it before it changes the published knowledge base."
            ),
            "fields": [
                {
                    "id": "feedback",
                    "type": "textarea",
                    "label": "Feedback for the next extraction",
                    "description": "Optional feedback retained with a rejected candidate.",
                    "required": False,
                }
            ],
            "actions": [
                {"id": "apply_once", "label": "Approve Knowledge ChangeSet", "style": "primary"},
                {"id": "reject", "label": "Reject", "style": "danger"},
            ],
            "change": {
                "tool": "knowledge_publish",
                "changeset_id": changeset_id,
                "project_id": project_id,
            },
            "created_at": now,
            "_server": {
                "kind": "knowledge_changeset_approval",
                "project_id": project_id,
                "changeset_id": changeset_id,
            },
        }
        previous = deepcopy(session.metadata)
        session.metadata[INTERACTION_STATE_KEY] = request
        try:
            self._sessions.save(session)
        except BaseException:
            session.metadata.clear()
            session.metadata.update(previous)
            raise
        if self._runtime_events is not None:
            await self._runtime_events.publish(InteractionStateChanged(
                context=RuntimeEventContext(
                    channel=ctx.channel,
                    chat_id=ctx.chat_id,
                    session_key=ctx.session_key,
                    metadata=dict(ctx.metadata or {}),
                ),
                session_metadata=dict(session.metadata),
            ))
        return ToolResult.error(
            f"Approval required for Knowledge ChangeSet {changeset_id}. "
            "Wait for the user's response, then replay knowledge_publish or knowledge_approve."
        )

    @staticmethod
    def _error(error: Exception) -> ToolResult:
        if isinstance(error, (KnowledgeNotFoundError, KnowledgeStoreError)):
            return ToolResult.error(f"Error: {error}")
        return ToolResult.error(f"Error: {error}")


@tool_parameters(tool_parameters_schema(
    path=StringSchema(
        "Directory containing raw source documents, relative to the current workspace.",
        min_length=1,
        max_length=2000,
    ),
    project_id=StringSchema("Existing Knowledge project id.", max_length=128, nullable=True),
    title=StringSchema("Project title when creating a project.", max_length=240, nullable=True),
    schema=StringSchema("Schema profile name.", max_length=80, nullable=True),
    max_files=IntegerSchema(description="Maximum source files to scan.", minimum=1, maximum=10000, nullable=True),
    required=["path"],
))
class KnowledgeScanTool(Tool, _KnowledgeToolMixin):
    """Discover source files and create/update a Knowledge project manifest."""

    def __init__(self, workspace: str, sessions: SessionManager) -> None:
        _KnowledgeToolMixin.__init__(self, workspace, sessions)
        self._context_provider = KnowledgeContextProvider(sessions)

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("KnowledgeScanTool requires sessions")
        return cls(ctx.workspace, ctx.sessions)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "knowledge_scan"

    @property
    def description(self) -> str:
        return "Scan a workspace source directory and create/update the active durable Knowledge project manifest."

    def runtime_context_provider(self):
        return self._context_provider

    async def execute(
        self,
        path: str,
        project_id: str | None = None,
        title: str | None = None,
        schema: str | None = None,
        max_files: int | None = None,
        **_: Any,
    ) -> str:
        try:
            service = KnowledgeService(_store(self._workspace))
            project_id = project_id or self._context().get("project_id")
            result = service.scan(
                path,
                project_id=project_id,
                title=title,
                schema_name=schema or "default",
                max_files=max_files or 2000,
            )
            project = result["project"]
            task = _mapping(result.get("task"))
            self._set_context(
                task_id=task.get("id") if isinstance(task.get("id"), str) else None,
                project_id=project["id"],
                source_root=project["source_root"],
                phase="scanned",
            )
            session = self._session()
            if session is not None:
                session.metadata.pop(KNOWLEDGE_REQUESTED_METADATA, None)
                session.metadata.pop("knowledge_selection_pending", None)
                self._sessions.save(session)
            return _json(_bounded_scan_result(result))
        except Exception as error:
            return self._error(error)


@tool_parameters(tool_parameters_schema(
    project_id=StringSchema("Knowledge project id.", max_length=128),
    source_path=StringSchema(
        "One source-relative path returned by knowledge_scan.",
        min_length=1,
        max_length=2000,
    ),
    max_ocr_assets=IntegerSchema(
        description="Maximum extracted image assets to OCR in this bounded pass.",
        minimum=0,
        maximum=500,
        nullable=True,
    ),
    max_legacy_assets=IntegerSchema(
        description=(
            "Maximum PNG/JPEG assets to recover from a legacy DOC Data stream. "
            "Use 0 only to resume OCR from an existing normalization without reading the Data stream."
        ),
        minimum=0,
        maximum=50000,
        nullable=True,
    ),
    start_page=IntegerSchema(
        description="Optional 1-based first PDF page for this bounded normalization pass.",
        minimum=1,
        nullable=True,
    ),
    end_page=IntegerSchema(
        description="Optional inclusive last PDF page; defaults to a five-page batch.",
        minimum=1,
        nullable=True,
    ),
    required=["project_id", "source_path"],
))
class KnowledgeNormalizeTool(Tool, _KnowledgeToolMixin):
    """Normalize one compound source into bounded text and evidence assets."""

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("KnowledgeNormalizeTool requires sessions")
        return cls(ctx.workspace, ctx.sessions)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "knowledge_normalize"

    @property
    def description(self) -> str:
        return (
            "Normalize one scanned DOC/DOCX/PDF/image into derived text, tables, formulas, images, "
            "and bounded OCR evidence. Use this before knowledge_extract for compound or scanned sources. "
            "Large legacy DOC Data streams are processed incrementally and never injected into context."
        )

    @property
    def exclusive(self) -> bool:
        return True

    async def execute(
        self,
        project_id: str,
        source_path: str,
        max_ocr_assets: int | None = None,
        max_legacy_assets: int | None = None,
        start_page: int | None = None,
        end_page: int | None = None,
        **_: Any,
    ) -> str:
        try:
            store = _store(self._workspace)
            project = store.get_project(project_id)
            source = next(
                (item for item in project.sources if item.relative_path == source_path),
                None,
            )
            if source is None:
                return ToolResult.error("Error: source_path is not present in the project manifest.")
            raw_relative = source.raw_relative_path or source.relative_path
            raw_path = store.raw_path(project_id, raw_relative)
            source_key = source.sha256[:16] if source.sha256 else uuid4().hex[:16]
            output = store.project_path(project_id) / "knowledge" / "normalized" / source_key
            result = normalize_source(
                raw_path,
                output,
                max_ocr_assets=max_ocr_assets if max_ocr_assets is not None else 20,
                max_legacy_assets=max_legacy_assets if max_legacy_assets is not None else 20_000,
                start_page=start_page,
                end_page=end_page,
            )
            source.status = "normalized"
            source.metadata["normalization_path"] = output.relative_to(
                store.project_path(project_id)
            ).as_posix()
            source.metadata["normalized_asset_count"] = len(_items(result.get("assets")))
            source.metadata["ocr_asset_count"] = len(_items(result.get("ocr")))
            store.save_project(project)
            bounded = dict(result)
            assets = _items(result.get("assets"))
            ocr = _items(result.get("ocr"))
            bounded["assets"] = assets[:20]
            bounded["ocr"] = ocr[:20]
            bounded["asset_count"] = len(assets)
            bounded["ocr_count"] = len(ocr)
            bounded["normalization_path"] = source.metadata["normalization_path"]
            self._set_context(project_id=project_id, phase="normalized")
            return _json(bounded)
        except Exception as error:
            return self._error(error)


_EVIDENCE_SCHEMA = ObjectSchema(
    properties={
        "id": StringSchema("Stable evidence id.", max_length=128, nullable=True),
        "source_path": StringSchema("Source-relative path returned by knowledge_scan.", max_length=2000),
        "page_number": IntegerSchema(description="1-based PDF/DOCX page number when available.", minimum=1, nullable=True),
        "start_line": IntegerSchema(description="1-based bounded start line.", minimum=1, nullable=True),
        "end_line": IntegerSchema(description="1-based bounded end line.", minimum=1, nullable=True),
        "quote": StringSchema("Exact bounded evidence text; do not paraphrase it as a quote.", max_length=20000, nullable=True),
        "image_path": StringSchema("Workspace-relative image evidence path.", max_length=2000, nullable=True),
        "extraction_method": StringSchema("Reader method, for example pdf_pages or vision_ocr.", max_length=80, nullable=True),
        "confidence": NumberSchema(description="Optional confidence value between 0 and 1.", minimum=0, maximum=1, nullable=True),
        "metadata": ObjectSchema(additional_properties=True),
    },
    required=["source_path"],
    description="A bounded source observation with a path and optional line/page/image anchor.",
    additional_properties=True,
)

_PAGE_SCHEMA = ObjectSchema(
    properties={
        "type": StringSchema(
            "Page type. Use concept, entity, source, query, comparison, or synthesis.",
            enum=PAGE_TYPES,
        ),
        "title": StringSchema("Human-readable page title.", min_length=1, max_length=240),
        "slug": StringSchema("Stable page slug; omit only when it can be derived from title.", max_length=240),
        "body": StringSchema(
            "Non-empty Markdown正文. Include the extracted explanation, not only a heading.",
            min_length=1,
            max_length=20000,
        ),
        "tags": ArraySchema(StringSchema("A concise semantic tag."), max_items=30),
        "related": ArraySchema(StringSchema("Related page title or slug."), max_items=50),
        "sources": ArraySchema(StringSchema("Source-relative path from knowledge_scan."), max_items=20),
        "source_path": StringSchema("Primary source-relative path.", max_length=2000),
        "evidence": ArraySchema(_EVIDENCE_SCHEMA, description="Bounded evidence for this page draft.", max_items=20),
        "metadata": ObjectSchema(additional_properties=True),
    },
    required=["type", "title", "body"],
    description="Typed page draft. Every page must contain a substantive non-empty body.",
    additional_properties=True,
)
_RELATION_SCHEMA = ObjectSchema(additional_properties=True)
_CLAIM_SCHEMA = ObjectSchema(
    properties={
        "id": StringSchema("Stable claim id.", max_length=128, nullable=True),
        "subject": StringSchema("Claim subject.", min_length=1, max_length=240),
        "predicate": StringSchema("Claim predicate.", min_length=1, max_length=160),
        "object": StringSchema("Claim object/value.", min_length=1, max_length=4000),
        "evidence": ArraySchema(_EVIDENCE_SCHEMA, max_items=20),
        "source_path": StringSchema("Primary source-relative path.", max_length=2000, nullable=True),
        "confidence": NumberSchema(description="Optional numeric confidence between 0 and 1.", minimum=0, maximum=1, nullable=True),
        "status": StringSchema("asserted, uncertain, conflict, retracted, or confirmed.", max_length=40, nullable=True),
        "metadata": ObjectSchema(additional_properties=True),
    },
    required=["subject", "predicate", "object"],
    description="Fact-level assertion. Uncertain or conflicting content must be marked, never invented as certain.",
    additional_properties=True,
)
_ENTITY_SCHEMA = ObjectSchema(
    properties={
        "name": StringSchema("Canonical entity name.", min_length=1, max_length=240),
        "type": StringSchema("Entity page type; normally entity.", max_length=40),
        "description": StringSchema(
            "Substantive entity正文. Do not submit an empty description.",
            min_length=1,
            max_length=12000,
        ),
        "tags": ArraySchema(StringSchema("Semantic tag for this entity."), max_items=30),
        "related": ArraySchema(StringSchema("Related page title or slug."), max_items=50),
        "source_path": StringSchema("Source-relative path.", max_length=2000),
        "metadata": ObjectSchema(additional_properties=True),
        "evidence": ArraySchema(_EVIDENCE_SCHEMA, max_items=20),
    },
    required=["name", "description"],
    description="Structured entity record. Include description, tags, and related pages when known.",
    additional_properties=True,
)


def _load_ir_draft(store: KnowledgeStore, project_id: str, relative_path: str) -> dict[str, Any]:
    """Load a bounded JSON draft from inside one Knowledge project."""

    project_root = store.project_path(project_id).resolve()
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError("ir_draft_path must be relative to the Knowledge project")
    draft_path = (project_root / candidate).resolve()
    try:
        draft_path.relative_to(project_root)
    except ValueError as error:
        raise ValueError("ir_draft_path escapes the Knowledge project") from error
    if draft_path.suffix.lower() != ".json":
        raise ValueError("ir_draft_path must reference a JSON file")
    if not draft_path.is_file():
        raise FileNotFoundError(f"Knowledge IR draft not found: {relative_path}")
    if draft_path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("Knowledge IR draft exceeds the 5 MiB limit")
    value = json.loads(draft_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Knowledge IR draft must contain one JSON object")
    draft = cast(dict[str, Any], value)
    draft_project = str(draft.get("project_id") or "")
    if draft_project and draft_project != project_id:
        raise ValueError("Knowledge IR draft project_id does not match the active project")
    return draft


@tool_parameters(tool_parameters_schema(
    project_id=StringSchema("Knowledge project id.", max_length=128),
    source_path=StringSchema("One source path returned by knowledge_scan.", min_length=1, max_length=2000),
    ir_draft_path=StringSchema(
        "Optional project-relative JSON draft path. Use for large IR payloads that would overflow "
        "one tool call; the file must stay inside this Knowledge project and is validated before import.",
        max_length=2000,
        nullable=True,
    ),
    pages=ArraySchema(_PAGE_SCHEMA, description="Knowledge Page drafts in the intermediate representation.", max_items=200),
    relations=ArraySchema(_RELATION_SCHEMA, description="Typed relations with source evidence and optional confidence.", max_items=500),
    entities=ArraySchema(_ENTITY_SCHEMA, description="Optional extracted entity records.", max_items=500),
    claims=ArraySchema(_CLAIM_SCHEMA, description="Fact-level claims grounded in bounded evidence.", max_items=1000),
    evidence=ArraySchema(_EVIDENCE_SCHEMA, description="Shared bounded source observations.", max_items=1000),
    review_hints=ArraySchema(ObjectSchema(additional_properties=True), description="Potential conflicts, missing pages, or confirmation questions.", max_items=200),
    relation_confidence=ObjectSchema(additional_properties=True),
    notes=StringSchema("Extraction notes.", max_length=4000, nullable=True),
    required=["project_id", "source_path"],
))
class KnowledgeExtractTool(Tool, _KnowledgeToolMixin):
    """Persist structured Knowledge IR produced from one source document."""

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("KnowledgeExtractTool requires sessions")
        return cls(ctx.workspace, ctx.sessions)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "knowledge_extract"

    @property
    def description(self) -> str:
        return (
            "Save structured Knowledge IR for one source; do not generate wiki Markdown directly. "
            "Every page requires type, title, and substantive body. Every entity requires name "
            "and substantive description; include semantic tags and related page references. "
            "Submit fact-level claims and bounded evidence whenever a statement is factual. "
            "Relations must retain source_path plus evidence or evidence_refs. PDF/DOCX/image "
            "sources require bounded page/line/image evidence; never inject an entire source or wiki. "
            "For a large IR, first write one JSON draft inside the Knowledge project and pass its "
            "project-relative path as ir_draft_path instead of expanding the payload in context. "
            "Mark uncertain or conflicting content as uncertain/conflict or a review hint. "
            "Use only source-relative paths returned by knowledge_scan."
        )

    async def execute(
        self,
        project_id: str,
        source_path: str,
        ir_draft_path: str | None = None,
        pages: list[dict[str, Any]] | None = None,
        relations: list[dict[str, Any]] | None = None,
        entities: list[dict[str, Any]] | None = None,
        claims: list[dict[str, Any]] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        review_hints: list[dict[str, Any]] | None = None,
        relation_confidence: dict[str, Any] | None = None,
        notes: str | None = None,
        **_: Any,
    ) -> str:
        try:
            store = _store(self._workspace)
            if ir_draft_path:
                draft = _load_ir_draft(store, project_id, ir_draft_path)
                draft_source = str(draft.get("source_path") or "")
                if draft_source and draft_source != source_path:
                    raise ValueError(
                        "Knowledge IR draft source_path does not match the requested source_path"
                    )
                pages = pages if pages is not None else cast(list[dict[str, Any]] | None, draft.get("pages"))
                relations = relations if relations is not None else cast(list[dict[str, Any]] | None, draft.get("relations"))
                entities = entities if entities is not None else cast(list[dict[str, Any]] | None, draft.get("entities"))
                claims = claims if claims is not None else cast(list[dict[str, Any]] | None, draft.get("claims"))
                evidence = evidence if evidence is not None else cast(list[dict[str, Any]] | None, draft.get("evidence"))
                review_hints = review_hints if review_hints is not None else cast(list[dict[str, Any]] | None, draft.get("review_hints"))
                relation_confidence = relation_confidence if relation_confidence is not None else cast(dict[str, Any] | None, draft.get("relation_confidence"))
                notes = notes if notes is not None else str(draft.get("notes") or "")
            result = KnowledgeService(store).extract(
                project_id,
                source_path,
                pages=pages,
                relations=relations,
                entities=entities,
                notes=notes or "",
                claims=claims,
                evidence=evidence,
                review_hints=review_hints,
                relation_confidence=relation_confidence,
            )
            self._set_context(project_id=project_id, phase="extracting")
            return _json(result)
        except Exception as error:
            return self._error(error)


@tool_parameters(tool_parameters_schema(
    project_id=StringSchema("Knowledge project id; defaults to the active project.", max_length=128, nullable=True),
    candidate=BooleanSchema(description="Compile into an approval-gated candidate; defaults to true.", default=True, nullable=True),
    reason=StringSchema("Why this candidate should update the Knowledge project.", max_length=4000, nullable=True),
    required=[],
))
class KnowledgeCompileTool(Tool, _KnowledgeToolMixin):
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("KnowledgeCompileTool requires sessions")
        return cls(ctx.workspace, ctx.sessions, ctx.runtime_events)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "knowledge_compile"

    @property
    def description(self) -> str:
        return "Compile saved Knowledge IR into an approval-gated candidate Wiki/graph; do not mutate the published Wiki before approval."

    async def execute(
        self,
        project_id: str | None = None,
        candidate: bool | None = True,
        reason: str | None = None,
        **_: Any,
    ) -> str:
        try:
            project_id = project_id or self._context().get("project_id")
            if not project_id:
                return ToolResult.error("Error: provide project_id or call knowledge_scan first.")
            service = KnowledgeService(_store(self._workspace))
            result = service.compile_candidate(project_id, reason=reason or "") if candidate is not False else service.compile(project_id)
            changeset = _mapping(result.get("changeset"))
            self._set_context(
                project_id=project_id,
                phase="candidate_review" if changeset else "compiled",
                changeset_id=changeset.get("id") if isinstance(changeset.get("id"), str) else None,
            )
            pages = _mapping_items(result.get("pages"))
            compact = dict(result)
            compact["project"] = _project_summary(result.get("project"))
            compact["page_count"] = len(pages)
            compact["pages"] = [
                {
                    key: page.get(key)
                    for key in ("type", "title", "slug", "source_path", "sources")
                    if key in page
                }
                for page in pages[:100]
            ]
            compact["pages_truncated"] = len(pages) > len(compact["pages"])
            return _json(compact)
        except Exception as error:
            return self._error(error)


@tool_parameters(tool_parameters_schema(
    project_id=StringSchema("Knowledge project id; defaults to the active project.", max_length=128, nullable=True),
    changeset_id=StringSchema("Approval-gated candidate ChangeSet id.", max_length=128, nullable=True),
    required=[],
))
class KnowledgeValidateTool(Tool, _KnowledgeToolMixin):
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("KnowledgeValidateTool requires sessions")
        return cls(ctx.workspace, ctx.sessions)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "knowledge_validate"

    @property
    def description(self) -> str:
        return "Validate a candidate or published Knowledge project and persist typed review issues before any apply operation."

    async def execute(
        self,
        project_id: str | None = None,
        changeset_id: str | None = None,
        **_: Any,
    ) -> str:
        try:
            project_id = project_id or self._context().get("project_id")
            if not project_id:
                return ToolResult.error("Error: provide project_id or call knowledge_scan first.")
            service = KnowledgeService(_store(self._workspace))
            active_changeset = changeset_id
            if not active_changeset:
                active_changeset = self._context().get("changeset_id")
            result = (
                service.validate_candidate(project_id, active_changeset)
                if active_changeset
                else service.review(project_id)
            )
            self._set_context(
                project_id=project_id,
                phase="validated" if result["passed"] else "validation_failed",
            )
            compact = dict(result)
            if isinstance(compact.get("issues"), list):
                compact["issues"] = compact["issues"][:50]
                compact["issues_truncated"] = result.get("issue_count", 0) > len(compact["issues"])
            return _json(compact)
        except Exception as error:
            return self._error(error)


@tool_parameters(tool_parameters_schema(
    project_id=StringSchema("Knowledge project id; defaults to the active project.", max_length=128, nullable=True),
    changeset_id=StringSchema("Candidate ChangeSet id returned by knowledge_compile.", max_length=128, nullable=True),
    required=[],
))
class KnowledgePublishTool(Tool, _KnowledgeToolMixin):
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("KnowledgePublishTool requires sessions")
        return cls(ctx.workspace, ctx.sessions, ctx.runtime_events)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "knowledge_publish"

    @property
    def description(self) -> str:
        return (
            "Apply a compiled Knowledge ChangeSet using the current workspace execution policy: "
            "Auto Apply commits it directly, Ask Before Apply creates a review request, and "
            "Read-only blocks the mutation."
        )

    async def execute(
        self,
        project_id: str | None = None,
        changeset_id: str | None = None,
        **_: Any,
    ) -> str:
        try:
            project_id = project_id or self._context().get("project_id")
            if not project_id:
                return ToolResult.error("Error: provide project_id or call knowledge_scan first.")
            service = KnowledgeService(_store(self._workspace))
            active_changeset = changeset_id or self._context().get("changeset_id")
            if active_changeset:
                approval = await self._request_knowledge_approval(
                    project_id=project_id,
                    changeset_id=active_changeset,
                )
                if approval is not None:
                    return approval
                execution_policy = self._effective_execution_policy()
                result = service.approve_changeset(
                    project_id,
                    active_changeset,
                    reviewer="agent" if execution_policy == "auto" else "user",
                )
            else:
                return ToolResult.error(
                    "Error: compile a candidate first. Knowledge publish cannot bypass the "
                    "ChangeSet and human approval gate."
                )
            if result.get("published") or result.get("applied"):
                self._set_context(project_id=project_id, phase="published")
            compact = dict(result)
            compact["execution_policy"] = execution_policy
            compact["project"] = _project_summary(result.get("project"))
            return _json(compact)
        except Exception as error:
            return self._error(error)


@tool_parameters(tool_parameters_schema(
    project_id=StringSchema("Knowledge project id; defaults to the active project.", max_length=128, nullable=True),
    changeset_id=StringSchema("Candidate ChangeSet id returned by knowledge_compile.", max_length=128),
    reviewer=StringSchema("Human reviewer identity.", max_length=160, nullable=True),
    required=["changeset_id"],
))
class KnowledgeApproveTool(Tool, _KnowledgeToolMixin):
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("KnowledgeApproveTool requires sessions")
        return cls(ctx.workspace, ctx.sessions, ctx.runtime_events)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "knowledge_approve"

    @property
    def description(self) -> str:
        return (
            "Apply a Knowledge ChangeSet. Auto Apply commits it directly; Ask Before Apply "
            "requires the user's structured approval, and Read-only blocks it."
        )

    async def execute(
        self,
        changeset_id: str,
        project_id: str | None = None,
        reviewer: str | None = None,
        **_: Any,
    ) -> str:
        try:
            project_id = project_id or self._context().get("project_id")
            if not project_id:
                return ToolResult.error("Error: provide project_id or call knowledge_scan first.")
            approval = await self._request_knowledge_approval(
                project_id=project_id,
                changeset_id=changeset_id,
            )
            if approval is not None:
                return approval
            execution_policy = self._effective_execution_policy()
            result = KnowledgeService(_store(self._workspace)).approve_changeset(
                project_id,
                changeset_id,
                reviewer=reviewer or ("agent" if execution_policy == "auto" else "user"),
            )
            if result.get("applied"):
                self._set_context(project_id=project_id, phase="published")
            payload = dict(result)
            payload["execution_policy"] = execution_policy
            return _json(payload)
        except Exception as error:
            return self._error(error)


@tool_parameters(tool_parameters_schema(
    project_id=StringSchema("Knowledge project id; defaults to the active project.", max_length=128, nullable=True),
    changeset_id=StringSchema("Candidate ChangeSet id returned by knowledge_compile.", max_length=128),
    feedback=StringSchema("Human feedback for a rejected candidate.", max_length=4000, nullable=True),
    reviewer=StringSchema("Human reviewer identity.", max_length=160, nullable=True),
    required=["changeset_id"],
))
class KnowledgeRejectTool(Tool, _KnowledgeToolMixin):
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("KnowledgeRejectTool requires sessions")
        return cls(ctx.workspace, ctx.sessions, ctx.runtime_events)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "knowledge_reject"

    @property
    def description(self) -> str:
        return "Reject a Knowledge candidate without modifying the published Wiki and preserve reviewer feedback."

    async def execute(
        self,
        changeset_id: str,
        project_id: str | None = None,
        feedback: str | None = None,
        reviewer: str | None = None,
        **_: Any,
    ) -> str:
        try:
            project_id = project_id or self._context().get("project_id")
            if not project_id:
                return ToolResult.error("Error: provide project_id or call knowledge_scan first.")
            return _json(KnowledgeService(_store(self._workspace)).reject_changeset(
                project_id,
                changeset_id,
                feedback=feedback or "",
                reviewer=reviewer or "user",
            ))
        except Exception as error:
            return self._error(error)


@tool_parameters(tool_parameters_schema(
    query=StringSchema("Case-insensitive full-text query.", min_length=1, max_length=400),
    project_id=StringSchema("Knowledge project id; defaults to selected/active project.", max_length=128, nullable=True),
    mode=StringSchema(
        "Retrieval mode: vector, graph, or hybrid (default).",
        enum=("vector", "graph", "hybrid"),
        nullable=True,
    ),
    limit=IntegerSchema(description="Maximum matches.", minimum=1, maximum=50, nullable=True),
    page_type=StringSchema("Optional page type filter, for example concept or entity.", max_length=40, nullable=True),
    tag=StringSchema("Optional tag filter.", max_length=100, nullable=True),
    source_path=StringSchema("Optional source relative path filter.", max_length=2000, nullable=True),
    expand_hops=IntegerSchema(
        description="Graph expansion depth for graph/hybrid mode (0, 1, or 2).",
        minimum=0,
        maximum=2,
        nullable=True,
    ),
    required=["query"],
))
class KnowledgeSearchTool(Tool, _KnowledgeToolMixin):
    """Bounded retrieval for a selected Knowledge project."""

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("KnowledgeSearchTool requires sessions")
        return cls(ctx.workspace, ctx.sessions)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "knowledge_search"

    @property
    def description(self) -> str:
        return (
            "Search the selected/published Knowledge wiki with bounded vector, graph, or hybrid retrieval. "
            "Return concise document metadata, graph relations, and source-linked snippets; never inject the whole Wiki."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        query: str,
        project_id: str | None = None,
        mode: str | None = None,
        limit: int | None = None,
        page_type: str | None = None,
        tag: str | None = None,
        source_path: str | None = None,
        expand_hops: int | None = None,
        **_: Any,
    ) -> str:
        project_id = project_id or self._context().get("selected_project_id") or self._context().get("project_id")
        request = current_request_context()
        if not project_id and request is not None:
            selected = request.metadata.get(KNOWLEDGE_PROJECT_ID_METADATA)
            if isinstance(selected, str):
                project_id = selected.strip()
        if not project_id:
            return ToolResult.error("Error: select a Knowledge project or provide project_id.")
        try:
            store = _store(self._workspace)
            project = store.get_project(project_id)
            needle = query.strip().casefold()
            options = resolve_search_options(
                mode=mode,
                limit=limit,
                expand_hops=expand_hops,
            )
            preferences = options["preferences"]
            retrieval = KnowledgeRetriever(
                store,
                embedding_backend=preferences.embedding_backend,
                embedding_model=preferences.embedding_model,
            ).search(
                project,
                query,
                mode=str(options["mode"]),
                limit=int(options["limit"]),
                page_type=page_type,
                tag=tag,
                source_path=source_path,
                expand_hops=int(options["expand_hops"]),
            )
            retrieval.retrieval.update({
                "parameter_mode": preferences.parameter_mode,
                "query_rewrite": preferences.query_rewrite,
                "configured_top_k": preferences.top_k,
                "configured_expand_hops": preferences.expand_hops,
                "embedding_backend": preferences.embedding_backend,
                "embedding_model": preferences.embedding_model,
            })
            matches = retrieval.documents
            source_filter = source_path.strip() if source_path else None
            for match in matches:
                page_path = store.wiki_root(project_id) / str(match.get("path", ""))
                try:
                    page_relative = page_path.relative_to(store.workspace).as_posix()
                except ValueError:
                    page_relative = str(match.get("path", ""))
                snippet = str(match.get("snippet") or "")[:1_200]
                start_line = int(match.get("start_line") or 1)
                end_line = max(start_line, int(match.get("end_line") or start_line))
                match["slug"] = Path(str(match.get("path", ""))).stem
                match["citation"] = {
                    "path": page_relative,
                    "start_line": start_line,
                    "end_line": end_line,
                    "quote": snippet,
                    "project_id": project.id,
                    "page_path": str(match.get("path", "")),
                }
                source_citations: list[dict[str, Any]] = []
                for source in [str(item) for item in _items(match.get("sources"))][:3]:
                    citation = _source_citation(store, project, source, needle)
                    if citation is not None:
                        source_citations.append(citation)
                match["source_citations"] = source_citations
            claim_matches: list[dict[str, Any]] = []
            for ir in store.list_ir(project_id):
                for claim in ir.claims:
                    if source_filter and claim.source_path != source_filter:
                        continue
                    haystack = " ".join((claim.subject, claim.predicate, claim.object, claim.status)).casefold()
                    evidence_matches = [
                        item for item in claim.evidence
                        if needle in str(item.quote).casefold()
                    ]
                    if needle not in haystack and not evidence_matches:
                        continue
                    claim_matches.append({
                        "id": claim.id,
                        "subject": claim.subject,
                        "predicate": claim.predicate,
                        "object": claim.object,
                        "status": claim.status,
                        "confidence": claim.confidence,
                        "source_path": claim.source_path or ir.source_path,
                        "evidence": [item.to_dict() for item in (claim.evidence or [])[:8]],
                    })
                    if len(claim_matches) >= min(limit or 10, 20):
                        break
                if len(claim_matches) >= min(limit or 10, 20):
                    break
            citations: list[dict[str, Any]] = []
            for match in matches:
                raw_citations = _mapping_items(match.get("source_citations"))
                citations.extend(raw_citations)
                if not raw_citations and isinstance(match.get("citation"), dict):
                    citations.append(match["citation"])
            for claim in claim_matches:
                for evidence in _mapping_items(claim.get("evidence")):
                    citation = _evidence_citation(store, project, evidence)
                    if citation is not None:
                        citations.append(citation)
            saved_citations = self._set_citations(citations, project_id=project_id)
            self._set_retrieval(retrieval.to_dict())
            self._set_context(selected_project_id=project_id, phase="retrieved")
            return _json({
                "version": 2,
                "project_id": project_id,
                "query": query,
                "mode": retrieval.mode,
                "documents": matches,
                "filters": {
                    "page_type": page_type,
                    "tag": tag,
                    "source_path": source_path,
                },
                "matches": matches,
                "relations": retrieval.relations,
                "claims": claim_matches,
                "citations": saved_citations,
                "citation_count": len(saved_citations),
                "retrieval": retrieval.retrieval,
            })
        except Exception as error:
            return self._error(error)


@tool_parameters(tool_parameters_schema(
    question=StringSchema("The user's knowledge question to investigate.", min_length=1, max_length=400),
    project_id=StringSchema("Knowledge project id; defaults to selected/active project.", max_length=128, nullable=True),
    queries=ArraySchema(
        StringSchema("One focused sub-query supplied by the Agent.", min_length=1, max_length=400),
        description="Optional query decomposition. At most four bounded searches are executed.",
        max_items=4,
        nullable=True,
    ),
    mode=StringSchema(
        "Retrieval mode for each sub-query: vector, graph, or hybrid (default).",
        enum=("vector", "graph", "hybrid"),
        nullable=True,
    ),
    budget=IntegerSchema(
        description="Maximum number of sub-queries to execute (1-4).",
        minimum=1,
        maximum=4,
        nullable=True,
    ),
    min_documents=IntegerSchema(
        description="Stop early once this many distinct documents have useful scores.",
        minimum=1,
        maximum=8,
        nullable=True,
    ),
    expand_hops=IntegerSchema(
        description="Graph expansion depth for each search (0, 1, or 2).",
        minimum=0,
        maximum=2,
        nullable=True,
    ),
    required=["question"],
))
class KnowledgeResearchTool(Tool, _KnowledgeToolMixin):
    """Bounded multi-query orchestration over the read-only Knowledge search tool."""

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("KnowledgeResearchTool requires sessions")
        return cls(ctx.workspace, ctx.sessions)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "knowledge_research"

    @property
    def description(self) -> str:
        return (
            "Investigate a complex question with at most four bounded knowledge_search calls. "
            "Provide focused sub-queries when useful; the tool merges documents, claims, relations, "
            "and citations and reports its stop reason. It never writes the Wiki or injects full documents."
        )

    @property
    def read_only(self) -> bool:
        return True

    @staticmethod
    def _unique_queries(question: str, queries: list[str] | None, budget: int) -> list[str]:
        values = queries or [question]
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = str(value).strip()
            key = normalized.casefold()
            if not normalized or key in seen:
                continue
            seen.add(key)
            result.append(normalized[:400])
            if len(result) >= budget:
                break
        return result or [question.strip()[:400]]

    @staticmethod
    def _merge_items(
        target: dict[str, dict[str, Any]],
        items: Any,
        query: str,
        *,
        key_field: str = "path",
    ) -> None:
        for item in _mapping_items(items):
            if key_field == "_relation_key":
                key = ":".join(
                    str(item.get(field) or "").strip()
                    for field in ("source", "target", "relation")
                )
            else:
                key = str(item.get(key_field) or item.get("id") or "").strip()
            if not key:
                continue
            current = target.get(key)
            if current is None:
                current = dict(item)
                if key_field == "_relation_key":
                    current["_relation_key"] = key
                current["matched_queries"] = [query]
                target[key] = current
                continue
            for field in ("score", "vector_score", "lexical_score", "graph_score", "confidence"):
                incoming = item.get(field)
                existing = current.get(field)
                if isinstance(incoming, (int, float)) and (not isinstance(existing, (int, float)) or incoming > existing):
                    current[field] = incoming
            matched = [str(value) for value in _items(current.get("matched_queries"))]
            current["matched_queries"] = matched
            if query not in matched:
                matched.append(query)
            for field in ("sources", "source_citations", "evidence"):
                incoming_values = _items(item.get(field))
                if not incoming_values:
                    continue
                existing_values = _items(current.get(field))
                current[field] = existing_values
                for value in incoming_values:
                    if value not in existing_values:
                        existing_values.append(value)

    async def execute(
        self,
        question: str,
        project_id: str | None = None,
        queries: list[str] | None = None,
        mode: str | None = None,
        budget: int | None = None,
        min_documents: int | None = None,
        expand_hops: int | None = None,
        **_: Any,
    ) -> str:
        context = self._context()
        project_id = project_id or context.get("selected_project_id") or context.get("project_id")
        request = current_request_context()
        if not project_id and request is not None:
            selected = request.metadata.get(KNOWLEDGE_PROJECT_ID_METADATA)
            if isinstance(selected, str):
                project_id = selected.strip()
        if not project_id:
            return ToolResult.error("Error: select a Knowledge project or provide project_id.")
        options = resolve_research_options(
            mode=mode,
            budget=budget,
            min_documents=min_documents,
            expand_hops=expand_hops,
        )
        preferences = options["preferences"]
        budget_value = int(options["budget"])
        min_documents_value = int(options["min_documents"])
        planned = self._unique_queries(
            question,
            allow_query_rewrite(queries, preferences=preferences),
            budget_value,
        )
        search_tool = KnowledgeSearchTool(  # pyright: ignore[reportAbstractUsage]
            self._workspace,
            self._sessions,
        )
        documents: dict[str, dict[str, Any]] = {}
        relations: dict[str, dict[str, Any]] = {}
        claims: dict[str, dict[str, Any]] = {}
        citations: list[dict[str, Any]] = []
        executed: list[str] = []
        failures: list[str] = []
        retrieval_meta: dict[str, Any] = {}
        stop_reason = "budget_exhausted"
        try:
            for sub_query in planned[:budget_value]:
                raw = await search_tool.execute(
                    query=sub_query,
                    project_id=project_id,
                    mode=str(options["mode"]),
                    limit=None,
                    expand_hops=int(options["expand_hops"]),
                )
                if getattr(raw, "is_error", False):
                    failures.append(f"{sub_query}: {str(raw)[:240]}")
                    continue
                try:
                    payload = _mapping(json.loads(str(raw)))
                except (TypeError, json.JSONDecodeError):
                    failures.append(f"{sub_query}: malformed search result")
                    continue
                executed.append(sub_query)
                self._merge_items(documents, payload.get("documents") or payload.get("matches"), sub_query)
                self._merge_items(
                    relations,
                    payload.get("relations"),
                    sub_query,
                    key_field="_relation_key",
                )
                for relation in relations.values():
                    relation.pop("_relation_key", None)
                self._merge_items(claims, payload.get("claims"), sub_query, key_field="id")
                for citation in _mapping_items(payload.get("citations")):
                    if citation not in citations:
                        citations.append(citation)
                if not retrieval_meta:
                    retrieval_meta = _mapping(payload.get("retrieval"))
                if len(documents) >= min_documents_value:
                    stop_reason = "evidence_sufficient"
                    break
            if not executed and failures:
                stop_reason = "no_results"
            elif not documents and stop_reason == "budget_exhausted":
                stop_reason = "no_results"
            result: dict[str, Any] = {
                "version": 2,
                "project_id": project_id,
                "query": question,
                "mode": str(options["mode"]),
                "documents": sorted(
                    documents.values(),
                    key=lambda item: (-float(item.get("score", 0.0)), str(item.get("path", "")).casefold()),
                )[:20],
                "relations": list(relations.values())[:40],
                "claims": list(claims.values())[:20],
                "citations": citations[:12],
                "retrieval": {
                    **retrieval_meta,
                    "agentic": True,
                    "planned_queries": planned,
                    "executed_queries": executed,
                    "iterations": len(executed),
                    "budget": budget_value,
                    "stop_reason": stop_reason,
                    "failures": failures[:4],
                },
            }
            saved_citations = self._set_citations(citations[:12], project_id=project_id)
            result["citations"] = saved_citations
            self._set_retrieval(result)
            self._set_context(selected_project_id=project_id, phase="researched")
            return _json(result)
        except Exception as error:
            return self._error(error)
