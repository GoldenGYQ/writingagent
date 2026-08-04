"""Structured Knowledge Runtime tools.

Extraction produces JSON-like Knowledge IR.  Only the compiler writes wiki
Markdown, which keeps the Agent Runtime's tool boundary explicit and makes
validation/rebuilds deterministic.
"""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import ToolContext, current_request_context
from nanobot.agent.tools.schema import (
    ArraySchema,
    IntegerSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.knowledge.compiler import parse_frontmatter
from nanobot.knowledge.context import (
    KNOWLEDGE_PROJECT_ID_METADATA,
    KnowledgeContextProvider,
    knowledge_context_raw,
    set_knowledge_context,
)
from nanobot.knowledge.service import KnowledgeService
from nanobot.knowledge.store import KnowledgeNotFoundError, KnowledgeStore, KnowledgeStoreError
from nanobot.session.manager import SessionManager


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _project_summary(project: Any) -> dict[str, Any]:
    if not isinstance(project, dict):
        return {}
    summary = {
        key: value
        for key, value in project.items()
        if key not in {"sources", "metadata"}
    }
    summary["source_count"] = len(project.get("sources", [])) if isinstance(project.get("sources"), list) else 0
    return summary


def _bounded_scan_result(result: dict[str, Any]) -> dict[str, Any]:
    documents = result.get("documents")
    if not isinstance(documents, list):
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
        "source_path": source.relative_path,
    }


def _store(default_workspace: str) -> KnowledgeStore:
    request = current_request_context()
    return KnowledgeStore(request.workspace or default_workspace if request else default_workspace)


class _KnowledgeToolMixin:
    def __init__(self, workspace: str, sessions: SessionManager) -> None:
        self._workspace = workspace
        self._sessions = sessions

    def _session(self) -> Any | None:
        request = current_request_context()
        if request is None or not request.session_key:
            return None
        return self._sessions.get_or_create(request.session_key)

    def _context(self) -> dict[str, str]:
        session = self._session()
        return knowledge_context_raw(session.metadata) if session else {}

    def _set_context(self, **values: str | None) -> dict[str, str]:
        session = self._session()
        if session is None:
            return {}
        context = set_knowledge_context(session.metadata, **values)
        self._sessions.save(session)
        return context

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
        return "Scan a workspace source directory and create a durable Knowledge project manifest."

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
            result = service.scan(
                path,
                project_id=project_id,
                title=title,
                schema_name=schema or "default",
                max_files=max_files or 2000,
            )
            project = result["project"]
            task = result.get("task") if isinstance(result.get("task"), dict) else {}
            self._set_context(
                task_id=task.get("id") if isinstance(task.get("id"), str) else None,
                project_id=project["id"],
                source_root=project["source_root"],
                phase="scanned",
            )
            return _json(_bounded_scan_result(result))
        except Exception as error:
            return self._error(error)


_PAGE_SCHEMA = ObjectSchema(additional_properties=True)
_RELATION_SCHEMA = ObjectSchema(additional_properties=True)
_ENTITY_SCHEMA = ObjectSchema(additional_properties=True)


@tool_parameters(tool_parameters_schema(
    project_id=StringSchema("Knowledge project id.", max_length=128),
    source_path=StringSchema("One source path returned by knowledge_scan.", min_length=1, max_length=2000),
    pages=ArraySchema(_PAGE_SCHEMA, description="Knowledge Page drafts in the intermediate representation.", max_items=200),
    relations=ArraySchema(_RELATION_SCHEMA, description="Typed relations with optional evidence.", max_items=500),
    entities=ArraySchema(_ENTITY_SCHEMA, description="Optional extracted entity records.", max_items=500),
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
        return "Save structured Knowledge IR for one source; do not generate wiki Markdown directly."

    async def execute(
        self,
        project_id: str,
        source_path: str,
        pages: list[dict[str, Any]] | None = None,
        relations: list[dict[str, Any]] | None = None,
        entities: list[dict[str, Any]] | None = None,
        notes: str | None = None,
        **_: Any,
    ) -> str:
        try:
            result = KnowledgeService(_store(self._workspace)).extract(
                project_id,
                source_path,
                pages=pages,
                relations=relations,
                entities=entities,
                notes=notes or "",
            )
            self._set_context(project_id=project_id, phase="extracting")
            return _json(result)
        except Exception as error:
            return self._error(error)


@tool_parameters(tool_parameters_schema(
    project_id=StringSchema("Knowledge project id; defaults to the active project.", max_length=128, nullable=True),
    required=[],
))
class KnowledgeCompileTool(Tool, _KnowledgeToolMixin):
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("KnowledgeCompileTool requires sessions")
        return cls(ctx.workspace, ctx.sessions)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "knowledge_compile"

    @property
    def description(self) -> str:
        return "Compile all saved Knowledge IR into merged typed wiki pages, index, log, and graph.json."

    async def execute(self, project_id: str | None = None, **_: Any) -> str:
        try:
            project_id = project_id or self._context().get("project_id")
            if not project_id:
                return ToolResult.error("Error: provide project_id or call knowledge_scan first.")
            result = KnowledgeService(_store(self._workspace)).compile(project_id)
            self._set_context(project_id=project_id, phase="compiled")
            pages = result.get("pages") if isinstance(result.get("pages"), list) else []
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
                if isinstance(page, dict)
            ]
            compact["pages_truncated"] = len(pages) > len(compact["pages"])
            return _json(compact)
        except Exception as error:
            return self._error(error)


@tool_parameters(tool_parameters_schema(
    project_id=StringSchema("Knowledge project id; defaults to the active project.", max_length=128, nullable=True),
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
        return "Validate frontmatter, source evidence, wikilinks, and graph consistency before publish."

    async def execute(self, project_id: str | None = None, **_: Any) -> str:
        try:
            project_id = project_id or self._context().get("project_id")
            if not project_id:
                return ToolResult.error("Error: provide project_id or call knowledge_scan first.")
            result = KnowledgeService(_store(self._workspace)).review(project_id)
            self._set_context(project_id=project_id, phase="validated" if result["passed"] else "validation_failed")
            compact = dict(result)
            if isinstance(compact.get("issues"), list):
                compact["issues"] = compact["issues"][:50]
                compact["issues_truncated"] = result.get("issue_count", 0) > len(compact["issues"])
            return _json(compact)
        except Exception as error:
            return self._error(error)


@tool_parameters(tool_parameters_schema(
    project_id=StringSchema("Knowledge project id; defaults to the active project.", max_length=128, nullable=True),
    required=[],
))
class KnowledgePublishTool(Tool, _KnowledgeToolMixin):
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("KnowledgePublishTool requires sessions")
        return cls(ctx.workspace, ctx.sessions)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "knowledge_publish"

    @property
    def description(self) -> str:
        return "Validate and publish a compiled Knowledge wiki and graph snapshot."

    async def execute(self, project_id: str | None = None, **_: Any) -> str:
        try:
            project_id = project_id or self._context().get("project_id")
            if not project_id:
                return ToolResult.error("Error: provide project_id or call knowledge_scan first.")
            result = KnowledgeService(_store(self._workspace)).publish(project_id)
            if result.get("published"):
                self._set_context(project_id=project_id, phase="published")
            compact = dict(result)
            compact["project"] = _project_summary(result.get("project"))
            return _json(compact)
        except Exception as error:
            return self._error(error)


@tool_parameters(tool_parameters_schema(
    query=StringSchema("Case-insensitive full-text query.", min_length=1, max_length=400),
    project_id=StringSchema("Knowledge project id; defaults to selected/active project.", max_length=128, nullable=True),
    limit=IntegerSchema(description="Maximum matches.", minimum=1, maximum=50, nullable=True),
    page_type=StringSchema("Optional page type filter, for example concept or entity.", max_length=40, nullable=True),
    tag=StringSchema("Optional tag filter.", max_length=100, nullable=True),
    source_path=StringSchema("Optional source relative path filter.", max_length=2000, nullable=True),
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
        return "Search only the selected/published Knowledge wiki and return bounded source-linked snippets."

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        query: str,
        project_id: str | None = None,
        limit: int | None = None,
        page_type: str | None = None,
        tag: str | None = None,
        source_path: str | None = None,
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
            type_filter = page_type.strip().casefold() if page_type else None
            tag_filter = tag.strip().casefold() if tag else None
            source_filter = source_path.strip() if source_path else None
            matches: list[dict[str, Any]] = []
            for path in store.wiki_root(project_id).rglob("*.md"):
                if path.name in {"index.md", "overview.md", "log.md"}:
                    continue
                content = path.read_text(encoding="utf-8")
                metadata, _ = parse_frontmatter(content)
                metadata_type = str(metadata.get("type") or "").casefold()
                metadata_tags = {
                    str(value).casefold()
                    for value in (metadata.get("tags") if isinstance(metadata.get("tags"), list) else [])
                }
                metadata_sources = [
                    str(value)
                    for value in (metadata.get("sources") if isinstance(metadata.get("sources"), list) else [])
                ]
                if type_filter and metadata_type != type_filter:
                    continue
                if tag_filter and tag_filter not in metadata_tags:
                    continue
                if source_filter and source_filter not in metadata_sources:
                    continue
                if needle not in content.casefold():
                    continue
                lines = content.splitlines()
                hit = next((index for index, line in enumerate(lines) if needle in line.casefold()), 0)
                start = max(0, hit - 2)
                end = min(len(lines), hit + 5)
                snippet = "\n".join(lines[start:end])[:800]
                page_path = path.relative_to(store.workspace).as_posix()
                source_citations = []
                for source in metadata_sources[:3]:
                    citation = _source_citation(store, project, source, needle)
                    if citation is not None:
                        source_citations.append(citation)
                matches.append({
                    "slug": path.stem,
                    "path": path.relative_to(store.project_path(project_id)).as_posix(),
                    "snippet": snippet,
                    "quote": snippet,
                    "start_line": start + 1,
                    "end_line": end,
                    "project_id": project.id,
                    "page_type": metadata_type,
                    "tags": sorted(metadata_tags),
                    "sources": metadata_sources,
                    "citation": {
                        "path": page_path,
                        "start_line": start + 1,
                        "end_line": end,
                        "quote": snippet,
                    },
                    "source_citations": source_citations,
                })
                if len(matches) >= min(limit or 10, 20):
                    break
            return _json({
                "project_id": project_id,
                "query": query,
                "filters": {
                    "page_type": page_type,
                    "tag": tag,
                    "source_path": source_path,
                },
                "matches": matches,
            })
        except Exception as error:
            return self._error(error)
