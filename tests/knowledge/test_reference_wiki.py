from __future__ import annotations

import json

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.knowledge import KnowledgeSearchTool
from nanobot.knowledge.store import KnowledgeStore
from nanobot.security.workspace_access import (
    bind_workspace_scope,
    build_workspace_scope,
    reset_workspace_scope,
)
from nanobot.session.manager import SessionManager


def test_reference_shaped_wiki_is_discovered_without_writing_metadata(tmp_path) -> None:
    project_root = tmp_path / "wikis" / "项目知识库"
    (project_root / "raw" / "sources" / "doc").mkdir(parents=True)
    (project_root / "wiki" / "concepts").mkdir(parents=True)
    (project_root / "schema.md").write_text("# Wiki 结构规范\n", encoding="utf-8")
    source = project_root / "raw" / "sources" / "doc" / "README.md"
    source.write_text("# Source\n\nKnowledge source.\n", encoding="utf-8")
    page = project_root / "wiki" / "concepts" / "Agent运行时.md"
    page.write_text(
        "---\n"
        "type: concept\n"
        "title: Agent运行时\n"
        "tags: [Agent]\n"
        "related: []\n"
        "sources: [\"raw/sources/doc/README.md\"]\n"
        "created: 2026-08-05\n"
        "updated: 2026-08-05\n"
        "---\n\n"
        "# Agent运行时\n\n"
        "A runtime concept.\n",
        encoding="utf-8",
    )

    store = KnowledgeStore(tmp_path)
    projects = store.list_projects()

    assert [project.id for project in projects] == ["项目知识库"]
    project = store.get_project("项目知识库")
    assert project.title == "项目知识库"
    assert project.metadata["read_only"] is True
    assert project.page_count == 1
    assert [source.relative_path for source in project.sources] == [
        "raw/sources/doc/README.md"
    ]
    assert project.sources[0].raw_relative_path == "sources/doc/README.md"
    assert not (project_root / "project.json").exists()


@pytest.mark.asyncio
async def test_reference_wiki_search_returns_raw_source_citation(tmp_path) -> None:
    project_root = tmp_path / "wikis" / "项目知识库"
    source_root = project_root / "raw" / "sources" / "doc"
    wiki_root = project_root / "wiki" / "concepts"
    source_root.mkdir(parents=True)
    wiki_root.mkdir(parents=True)
    (source_root / "runtime.md").write_text(
        "# Agent Runtime\n\nAgent Runtime is the execution boundary.\n",
        encoding="utf-8",
    )
    (wiki_root / "runtime.md").write_text(
        "---\n"
        "type: concept\n"
        "title: Agent Runtime\n"
        "tags: [runtime]\n"
        "related: []\n"
        "sources: [\"raw/sources/doc/runtime.md\"]\n"
        "created: 2026-08-05\n"
        "updated: 2026-08-05\n"
        "---\n\n"
        "# Agent Runtime\n\nAgent Runtime is the execution boundary.\n",
        encoding="utf-8",
    )

    project = KnowledgeStore(tmp_path).get_project("项目知识库")
    sessions = SessionManager(tmp_path)
    search = KnowledgeSearchTool(str(tmp_path), sessions)
    request = RequestContext(
        channel="websocket",
        chat_id="reference",
        session_key="websocket:reference",
        workspace=tmp_path,
        metadata={"knowledge_project_id": project.id},
    )
    scope = build_workspace_scope(tmp_path, "restricted", execution_policy="auto")
    token = bind_workspace_scope(scope)
    try:
        with request_context(request):
            result = await search.execute("Agent Runtime", project_id=project.id)
    finally:
        reset_workspace_scope(token)

    payload = json.loads(str(result))
    assert payload["project_id"] == "项目知识库"
    assert payload["citation_count"] == 1
    assert payload["citations"][0]["path"].endswith("raw/sources/doc/runtime.md")
    assert payload["citations"][0]["start_line"] == 1
