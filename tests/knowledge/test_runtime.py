from __future__ import annotations

import json

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.knowledge import KnowledgeScanTool, KnowledgeSearchTool
from nanobot.knowledge.compiler import parse_frontmatter
from nanobot.knowledge.context import (
    KNOWLEDGE_SOURCE_PENDING,
    KnowledgeContextProvider,
    set_knowledge_context,
)
from nanobot.knowledge.service import KnowledgeService
from nanobot.knowledge.store import KnowledgeStore, KnowledgeStoreError
from nanobot.session.manager import SessionManager


def _source_tree(tmp_path):
    raw = tmp_path / "raw" / "sources" / "doc"
    raw.mkdir(parents=True)
    (raw / "runtime.md").write_text(
        "# Agent Runtime\n\nAn execution layer for tool-calling agents.\n",
        encoding="utf-8",
    )
    (raw / "langgraph.md").write_text(
        "# LangGraph\n\nA graph-based orchestration framework.\n",
        encoding="utf-8",
    )
    return raw


def test_scan_extract_compile_validate_publish_reference_shape(tmp_path):
    _source_tree(tmp_path)
    service = KnowledgeService(KnowledgeStore(tmp_path))

    scanned = service.scan("raw/sources/doc", title="Agent Runtime Wiki")
    project = scanned["project"]
    project_id = project["id"]
    assert scanned["files"] == 2
    assert (tmp_path / "wikis" / project_id / "schema.md").exists()
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
        assert (tmp_path / "wikis" / project_id / relative).is_dir()
    assert (
        tmp_path / "wikis" / project_id / "raw" / "sources" / "runtime.md"
    ).read_text(encoding="utf-8").startswith("# Agent Runtime")
    manifest = tmp_path / "wikis" / project_id / "knowledge" / "manifest.json"
    assert json.loads(manifest.read_text(encoding="utf-8"))["project_id"] == project_id

    service.extract(
        project_id,
        "runtime.md",
        pages=[{
            "type": "concept",
            "title": "Agent Runtime",
            "slug": "agent-runtime",
            "body": "The runtime coordinates tools and provider turns.",
            "tags": ["runtime"],
            "related": ["langgraph"],
        }],
        relations=[{
            "source": "agent-runtime",
            "target": "langgraph",
            "relation": "implements",
            "evidence": "Both describe orchestration boundaries.",
        }],
    )
    service.extract(
        project_id,
        "langgraph.md",
        entities=[{
            "name": "LangGraph",
            "type": "entity",
            "description": "A graph-based orchestration framework.",
        }],
    )

    compiled = service.compile(project_id)
    assert compiled["graph"]["nodes"] == 2
    project_path = tmp_path / "wikis" / project_id
    concept = project_path / "wiki" / "concepts" / "agent-runtime.md"
    entity = project_path / "wiki" / "entities" / "LangGraph.md"
    assert concept.exists()
    assert entity.exists()
    metadata, body = parse_frontmatter(concept.read_text(encoding="utf-8"))
    assert metadata["type"] == "concept"
    assert metadata["sources"] == ["runtime.md"]
    assert "coordinates" in body
    assert (project_path / "wiki" / "index.md").read_text(encoding="utf-8").startswith("# Wiki Index")
    assert "raw/sources/runtime.md" in (project_path / "wiki" / "log.md").read_text(encoding="utf-8")

    validation = service.validate(project_id)
    assert validation["passed"] is True, validation
    review = service.review(project_id)
    assert review["review"]["status"] == "passed"
    assert (project_path / "knowledge" / "reviews" / f"{review['review']['id']}.json").exists()
    published = service.publish(project_id)
    assert published["published"] is True
    assert KnowledgeStore(tmp_path).get_project(project_id).phase == "published"
    graph = json.loads((project_path / "knowledge" / "graph" / "graph.json").read_text(encoding="utf-8"))
    assert any(edge["relation"] == "implements" for edge in graph["edges"])
    task = json.loads((project_path / "knowledge" / "task.json").read_text(encoding="utf-8"))
    assert task["status"] == "completed"
    assert task["phase"] == "published"


def test_initialize_creates_task_boundary_and_scan_reuses_project(tmp_path):
    source_root = _source_tree(tmp_path)
    service = KnowledgeService(KnowledgeStore(tmp_path))

    initialized = service.initialize(str(source_root), title="Deferred scan")
    project_id = initialized["project"]["id"]
    project_path = tmp_path / "wikis" / project_id
    assert initialized["task"]["phase"] == "scanning"
    assert (project_path / "project.json").exists()
    assert (project_path / "knowledge" / "task.json").exists()
    assert not list((project_path / "raw").rglob("*"))

    scanned = service.scan(str(source_root), project_id=project_id)
    assert scanned["project"]["id"] == project_id
    assert scanned["files"] == 2
    assert len(KnowledgeStore(tmp_path).list_projects()) == 1


@pytest.mark.asyncio
async def test_scan_tool_defaults_to_command_initialized_project(tmp_path):
    source_root = _source_tree(tmp_path)
    service = KnowledgeService(KnowledgeStore(tmp_path))
    initialized = service.initialize(str(source_root), title="Tool continuation")
    project_id = initialized["project"]["id"]
    sessions = SessionManager(tmp_path)
    session_key = "websocket:knowledge-tool"
    session = sessions.get_or_create(session_key)
    set_knowledge_context(
        session.metadata,
        project_id=project_id,
        task_id=initialized["task"]["id"],
        source_root=str(source_root),
        phase="scanning",
    )
    session.metadata["knowledge_requested"] = KNOWLEDGE_SOURCE_PENDING
    session.metadata["knowledge_selection_pending"] = True
    sessions.save(session)
    request = RequestContext(
        channel="websocket",
        chat_id="knowledge-tool",
        session_key=session_key,
        workspace=tmp_path,
    )
    with request_context(request):
        result = json.loads(await KnowledgeScanTool(str(tmp_path), sessions).execute(str(source_root)))
    assert result["project"]["id"] == project_id
    assert result["files"] == 2
    assert "knowledge_requested" not in sessions.get_or_create(session_key).metadata
    assert "knowledge_selection_pending" not in sessions.get_or_create(session_key).metadata
    assert len(KnowledgeStore(tmp_path).list_projects()) == 1


def test_compile_merges_existing_pages_and_does_not_duplicate_log(tmp_path):
    source_root = _source_tree(tmp_path)
    service = KnowledgeService(KnowledgeStore(tmp_path))
    project_id = service.scan(str(source_root), title="Merge test")["project"]["id"]

    service.extract(project_id, "runtime.md", pages=[{
        "type": "concept",
        "title": "Runtime",
        "slug": "runtime",
        "body": "Original explanation.",
    }])
    service.compile(project_id)
    service.extract(project_id, "runtime.md", pages=[{
        "type": "concept",
        "title": "Runtime",
        "slug": "runtime",
        "body": "Follow-up explanation.",
    }])
    service.compile(project_id)

    project_path = tmp_path / "wikis" / project_id
    content = (project_path / "wiki" / "concepts" / "runtime.md").read_text(encoding="utf-8")
    assert "Original explanation." in content
    assert "Follow-up explanation." in content
    log = (project_path / "wiki" / "log.md").read_text(encoding="utf-8")
    assert log.count("- Ingest: `raw/sources/runtime.md`") == 1


def test_source_path_cannot_escape_workspace_or_scan_root(tmp_path):
    source_root = _source_tree(tmp_path)
    service = KnowledgeService(KnowledgeStore(tmp_path))
    project_id = service.scan(str(source_root))["project"]["id"]

    with pytest.raises(KnowledgeStoreError, match="inside"):
        service.scan(str(tmp_path.parent))
    with pytest.raises(KnowledgeStoreError, match="outside"):
        service.extract(project_id, str(tmp_path / "outside.md"))


def test_source_page_keeps_reference_metadata_shape(tmp_path):
    source_root = _source_tree(tmp_path)
    service = KnowledgeService(KnowledgeStore(tmp_path))
    project_id = service.scan(str(source_root))["project"]["id"]
    service.extract(project_id, "runtime.md", pages=[{
        "type": "source",
        "title": "Agent Runtime source",
        "slug": "agent-runtime-source",
        "body": "A source-linked summary.",
        "metadata": {
            "authors": ["Nanobot Team"],
            "year": 2026,
            "url": "https://example.test/runtime",
            "venue": "Internal guide",
        },
    }])
    service.compile(project_id)
    page = (tmp_path / "wikis" / project_id / "wiki" / "sources" / "agent-runtime-source.md").read_text(encoding="utf-8")
    assert "authors:" in page
    assert "year: 2026" in page
    assert "url:" in page and "venue:" in page
    assert service.validate(project_id)["passed"] is True


def test_validate_rejects_invalid_relation_evidence_anchor(tmp_path):
    source_root = _source_tree(tmp_path)
    service = KnowledgeService(KnowledgeStore(tmp_path))
    project_id = service.scan(str(source_root))["project"]["id"]
    service.extract(
        project_id,
        "runtime.md",
        pages=[{
            "type": "concept",
            "title": "Runtime",
            "slug": "runtime",
            "body": "The runtime coordinates tools.",
        }],
        relations=[{
            "source": "runtime",
            "target": "langgraph",
            "relation": "implements",
            "evidence": "The source describes the relationship.",
            "source_path": "runtime.md",
            "start_line": 8,
            "end_line": 3,
        }],
    )
    service.extract(
        project_id,
        "langgraph.md",
        pages=[{
            "type": "concept",
            "title": "LangGraph",
            "slug": "langgraph",
            "body": "A graph-based framework.",
        }],
    )
    service.compile(project_id)

    validation = service.validate(project_id)

    assert validation["passed"] is False
    assert any(
        issue["kind"] == "evidence" and "line range is invalid" in issue["message"]
        for issue in validation["issues"]
    )


@pytest.mark.asyncio
async def test_search_returns_bounded_source_linked_snippets(tmp_path):
    source_root = _source_tree(tmp_path)
    service = KnowledgeService(KnowledgeStore(tmp_path))
    project_id = service.scan(str(source_root))["project"]["id"]
    service.extract(project_id, "runtime.md", pages=[{
        "type": "concept",
        "title": "Runtime",
        "slug": "runtime",
            "body": "The execution tool-calling loop is observable.",
        "tags": ["runtime"],
    }])
    service.compile(project_id)
    sessions = SessionManager(tmp_path)
    tool = KnowledgeSearchTool(str(tmp_path), sessions)
    with request_context(RequestContext(
        channel="websocket",
        chat_id="chat-1",
        session_key="websocket:chat-1",
        workspace=tmp_path,
        metadata={"knowledge_project_id": project_id},
    )):
        result = await tool.execute(query="execution", limit=1, page_type="concept", tag="runtime")
    payload = json.loads(str(result))
    assert payload["project_id"] == project_id
    assert payload["matches"][0]["path"].endswith("runtime.md")
    assert payload["matches"][0]["start_line"] <= payload["matches"][0]["end_line"]
    assert payload["matches"][0]["quote"] == payload["matches"][0]["snippet"]
    assert payload["matches"][0]["citation"]["path"].startswith("wikis/")
    assert payload["matches"][0]["source_citations"][0]["source_path"] == "runtime.md"
    assert payload["filters"]["page_type"] == "concept"


@pytest.mark.asyncio
async def test_knowledge_runtime_context_is_conditional_and_bounded(tmp_path):
    sessions = SessionManager(tmp_path)
    provider = KnowledgeContextProvider(sessions)
    request = RequestContext(
        channel="websocket",
        chat_id="chat-1",
        session_key="websocket:chat-1",
        workspace=tmp_path,
    )
    assert await provider(request) is None

    project_id = KnowledgeService(KnowledgeStore(tmp_path)).scan(
        str(_source_tree(tmp_path)),
        title="Runtime context task",
    )["project"]["id"]
    session = sessions.get_or_create(request.session_key)
    set_knowledge_context(session.metadata, project_id=project_id, source_root="raw")
    sessions.save(session)
    block = await provider(request)
    assert block is not None
    assert "[Knowledge Runtime]" in block.content
    assert "Knowledge Task:" in block.content
    assert "[Working Plan Guidance]" not in block.content
    assert len(block.content) <= 4_000


@pytest.mark.asyncio
async def test_knowledge_runtime_context_guides_source_selection(tmp_path):
    sessions = SessionManager(tmp_path)
    provider = KnowledgeContextProvider(sessions)
    request = RequestContext(
        channel="websocket",
        chat_id="chat-selection",
        session_key="websocket:chat-selection",
        workspace=tmp_path,
    )
    session = sessions.get_or_create(request.session_key)
    session.metadata["knowledge_requested"] = KNOWLEDGE_SOURCE_PENDING
    sessions.save(session)

    block = await provider(request)

    assert block is not None
    assert "without a source directory" in block.content
    assert "Ask the user for the source directory" in block.content
