from __future__ import annotations

import json

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.knowledge import KnowledgeSearchTool
from nanobot.knowledge.compiler import parse_frontmatter
from nanobot.knowledge.context import KnowledgeContextProvider, set_knowledge_context
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
    concept = project_path / "knowledge" / "wiki" / "concepts" / "agent-runtime.md"
    entity = project_path / "knowledge" / "wiki" / "entities" / "LangGraph.md"
    assert concept.exists()
    assert entity.exists()
    metadata, body = parse_frontmatter(concept.read_text(encoding="utf-8"))
    assert metadata["type"] == "concept"
    assert metadata["sources"] == ["runtime.md"]
    assert "coordinates" in body
    assert (project_path / "knowledge" / "wiki" / "index.md").read_text(encoding="utf-8").startswith("---")
    assert "runtime.md" in (project_path / "knowledge" / "wiki" / "log.md").read_text(encoding="utf-8")

    validation = service.validate(project_id)
    assert validation["passed"] is True, validation
    published = service.publish(project_id)
    assert published["published"] is True
    assert KnowledgeStore(tmp_path).get_project(project_id).phase == "published"
    graph = json.loads((project_path / "knowledge" / "graph" / "graph.json").read_text(encoding="utf-8"))
    assert any(edge["relation"] == "implements" for edge in graph["edges"])


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
    content = (project_path / "knowledge" / "wiki" / "concepts" / "runtime.md").read_text(encoding="utf-8")
    assert "Original explanation." in content
    assert "Follow-up explanation." in content
    log = (project_path / "knowledge" / "wiki" / "log.md").read_text(encoding="utf-8")
    assert log.count("- Ingest: `runtime.md`") == 1


def test_source_path_cannot_escape_workspace_or_scan_root(tmp_path):
    source_root = _source_tree(tmp_path)
    service = KnowledgeService(KnowledgeStore(tmp_path))
    project_id = service.scan(str(source_root))["project"]["id"]

    with pytest.raises(KnowledgeStoreError, match="inside"):
        service.scan(str(tmp_path.parent))
    with pytest.raises(KnowledgeStoreError, match="outside"):
        service.extract(project_id, str(tmp_path / "outside.md"))


@pytest.mark.asyncio
async def test_search_returns_bounded_source_linked_snippets(tmp_path):
    source_root = _source_tree(tmp_path)
    service = KnowledgeService(KnowledgeStore(tmp_path))
    project_id = service.scan(str(source_root))["project"]["id"]
    service.extract(project_id, "runtime.md", pages=[{
        "type": "concept",
        "title": "Runtime",
        "slug": "runtime",
        "body": "The tool-calling loop is observable.",
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
        result = await tool.execute(query="observable", limit=1)
    payload = json.loads(str(result))
    assert payload["project_id"] == project_id
    assert payload["matches"][0]["path"].endswith("runtime.md")
    assert payload["matches"][0]["start_line"] <= payload["matches"][0]["end_line"]


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

    session = sessions.get_or_create(request.session_key)
    set_knowledge_context(session.metadata, project_id="kb_demo", source_root="raw")
    sessions.save(session)
    block = await provider(request)
    assert block is not None
    assert "[Knowledge Runtime]" in block.content
    assert "[Working Plan Guidance]" not in block.content
    assert len(block.content) <= 4_000
