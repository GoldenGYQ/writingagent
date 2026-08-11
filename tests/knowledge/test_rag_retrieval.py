from __future__ import annotations

import json
import time

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.knowledge import KnowledgeResearchTool, KnowledgeSearchTool
from nanobot.config.schema import Config
from nanobot.knowledge.indexer import KnowledgeIndexer
from nanobot.knowledge.preferences import allow_query_rewrite, resolve_search_options
from nanobot.knowledge.retriever import KnowledgeRetriever
from nanobot.knowledge.service import KnowledgeService
from nanobot.knowledge.store import KnowledgeStore
from nanobot.session.manager import SessionManager


def _published_project(tmp_path):
    source_root = tmp_path / "sources"
    source_root.mkdir()
    (source_root / "runtime.md").write_text(
        "# Runtime\n\nSupervisor coordinates durable tool calls and keeps execution state observable.\n",
        encoding="utf-8",
    )
    (source_root / "graph.md").write_text(
        "# Graph\n\nGraph expansion provides bounded context around a selected runtime node.\n",
        encoding="utf-8",
    )
    service = KnowledgeService(KnowledgeStore(tmp_path))
    project_id = service.scan(str(source_root), title="RAG fixture")['project']['id']
    service.extract(
        project_id,
        "runtime.md",
        pages=[{
            "type": "concept",
            "title": "Supervisor",
            "slug": "supervisor",
            "body": (
                "Supervisor coordinates durable tool calls and keeps execution state observable. "
                "It delegates work to the Graph concept."
            ),
            "tags": ["runtime", "agent"],
            "sources": ["runtime.md"],
        }],
        relations=[{
            "source": "supervisor",
            "target": "graph",
            "relation": "uses",
            "evidence": "Supervisor delegates work to Graph.",
            "source_path": "runtime.md",
        }],
    )
    service.extract(
        project_id,
        "graph.md",
        pages=[{
            "type": "concept",
            "title": "Graph",
            "slug": "graph",
            "body": "Graph expansion provides bounded context around a selected runtime node.",
            "tags": ["runtime"],
            "sources": ["graph.md"],
        }],
    )
    service.compile(project_id)
    service.validate(project_id)
    service.publish(project_id)
    return project_id


def test_index_is_deterministic_bounded_and_rebuilds_on_page_change(tmp_path):
    project_id = _published_project(tmp_path)
    store = KnowledgeStore(tmp_path)
    project = store.get_project(project_id)
    indexer = KnowledgeIndexer(store)

    started = time.perf_counter()
    first = indexer.build(project)
    build_seconds = time.perf_counter() - started
    second = indexer.load_or_build(project)

    assert first.documents and first.chunks
    assert first.vectors.available
    assert [item.id for item in first.chunks] == [item.id for item in second.chunks]
    assert first.vectors.to_dict() == second.vectors.to_dict()
    assert build_seconds < 5.0
    retrieval_root = store.project_path(project_id) / "knowledge" / "retrieval"
    assert (retrieval_root / "manifest.json").exists()
    assert (retrieval_root / "chunks.jsonl").exists()
    assert (retrieval_root / "vectors.json").exists()

    page = store.wiki_root(project_id) / "concepts" / "supervisor.md"
    page.write_text(page.read_text(encoding="utf-8") + "\nNew evidence sentence.\n", encoding="utf-8")
    rebuilt = indexer.load_or_build(store.get_project(project_id))
    assert rebuilt.documents[0].content_hash != first.documents[0].content_hash or len(rebuilt.chunks) >= len(first.chunks)


def test_hybrid_retrieval_expands_graph_and_caps_context(tmp_path):
    project_id = _published_project(tmp_path)
    store = KnowledgeStore(tmp_path)
    project = store.get_project(project_id)
    result = KnowledgeRetriever(store).search(project, "Supervisor", mode="hybrid", limit=1, expand_hops=1)

    assert result.documents
    assert result.documents[0]["title"] == "Supervisor"
    assert result.relations
    assert result.retrieval["expanded_hops"] == 1
    assert result.retrieval["seed_nodes"]
    assert all(len(str(item["snippet"])) <= 1_200 for item in result.documents)
    assert len(result.documents) <= 1
    graph = json.loads((store.project_path(project_id) / "knowledge" / "graph" / "graph.json").read_text(encoding="utf-8"))
    assert graph["directed"] is False


def test_vector_and_graph_modes_are_explicit_and_bounded(tmp_path):
    project_id = _published_project(tmp_path)
    store = KnowledgeStore(tmp_path)
    project = store.get_project(project_id)
    retriever = KnowledgeRetriever(store)

    vector = retriever.search(project, "durable tool calls", mode="vector", limit=2, expand_hops=0)
    graph = retriever.search(project, "Supervisor", mode="graph", limit=2, expand_hops=2)

    assert vector.mode == "vector"
    assert vector.retrieval["expanded_hops"] == 0
    assert graph.mode == "graph"
    assert graph.retrieval["expanded_hops"] == 2
    assert len(graph.relations) <= 40
    assert all("snippet" in document for document in vector.documents)


def test_manual_retrieval_preferences_resolve_when_tool_arguments_are_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    config.agents.defaults.knowledge_retrieval.parameter_mode = "manual"
    config.agents.defaults.knowledge_retrieval.mode = "vector"
    config.agents.defaults.knowledge_retrieval.top_k = 14
    config.agents.defaults.knowledge_retrieval.expand_hops = 2
    monkeypatch.setattr("nanobot.knowledge.preferences.load_config", lambda: config)

    options = resolve_search_options(mode=None, limit=None, expand_hops=None)

    assert options["mode"] == "vector"
    assert options["limit"] == 14
    assert options["expand_hops"] == 2
    explicit = resolve_search_options(mode="graph", limit=2, expand_hops=0)
    assert explicit["mode"] == "graph"
    assert explicit["limit"] == 2
    assert explicit["expand_hops"] == 0


def test_query_rewrite_policy_is_bounded_and_does_not_call_an_llm() -> None:
    config = Config()
    config.agents.defaults.knowledge_retrieval.query_rewrite = "off"

    assert allow_query_rewrite(["first", "second"], preferences=config.agents.defaults.knowledge_retrieval) is None
    config.agents.defaults.knowledge_retrieval.query_rewrite = "manual"
    assert allow_query_rewrite(["first", "second"], preferences=config.agents.defaults.knowledge_retrieval) == ["first", "second"]


@pytest.mark.asyncio
async def test_search_tool_preserves_legacy_fields_and_retrieval_contract(tmp_path):
    project_id = _published_project(tmp_path)
    sessions = SessionManager(tmp_path)
    request = RequestContext(
        channel="websocket",
        chat_id="rag-test",
        session_key="websocket:rag-test",
        workspace=tmp_path,
        metadata={"knowledge_project_id": project_id},
    )
    with request_context(request):
        result = await KnowledgeSearchTool(str(tmp_path), sessions).execute(
            query="Supervisor",
            project_id=project_id,
            mode="hybrid",
            limit=2,
        )
    payload = json.loads(str(result))
    assert payload["version"] == 2
    assert payload["documents"] == payload["matches"]
    assert payload["relations"]
    assert payload["retrieval"]["index_algorithm"] == "sha256-feature-hash-v1"
    assert payload["retrieval"]["parameter_mode"] == "auto"
    assert payload["matches"][0]["citation"]["page_path"].endswith("supervisor.md")
    assert payload["matches"][0]["source_citations"]
    session = sessions.get_or_create(request.session_key)
    assert session.metadata["knowledge_retrieval"]["mode"] == "hybrid"


@pytest.mark.asyncio
async def test_research_tool_merges_queries_and_reports_bounded_stop_reason(tmp_path):
    project_id = _published_project(tmp_path)
    sessions = SessionManager(tmp_path)
    request = RequestContext(
        channel="websocket",
        chat_id="research-test",
        session_key="websocket:research-test",
        workspace=tmp_path,
        metadata={"knowledge_project_id": project_id},
    )
    tool = KnowledgeResearchTool(str(tmp_path), sessions)
    with request_context(request):
        result = await tool.execute(
            question="How does the runtime use graph context?",
            project_id=project_id,
            queries=["Supervisor durable tool calls", "Graph bounded context", "unused query"],
            budget=2,
            min_documents=2,
        )
    payload = json.loads(str(result))
    retrieval = payload["retrieval"]
    assert retrieval["agentic"] is True
    assert retrieval["budget"] == 2
    assert retrieval["iterations"] <= 2
    assert len(retrieval["executed_queries"]) <= 2
    assert retrieval["stop_reason"] in {"evidence_sufficient", "budget_exhausted", "no_results"}
    assert payload["documents"]
    assert len(payload["documents"]) <= 20
    assert len(payload["citations"]) <= 12
