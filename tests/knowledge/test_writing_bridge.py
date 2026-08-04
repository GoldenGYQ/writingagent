from __future__ import annotations

import json

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.knowledge import KnowledgeSearchTool
from nanobot.agent.tools.writing import WritingChangeSetTool
from nanobot.knowledge.service import KnowledgeService
from nanobot.knowledge.store import KnowledgeStore
from nanobot.security.workspace_access import (
    bind_workspace_scope,
    build_workspace_scope,
    reset_workspace_scope,
)
from nanobot.session.manager import SessionManager
from nanobot.writing.context import WritingContextProvider
from nanobot.writing.document import DocumentService
from nanobot.writing.store import WritingStore


@pytest.mark.asyncio
async def test_knowledge_search_citations_flow_into_writing_changeset(tmp_path):
    source_root = tmp_path / "raw" / "sources"
    source_root.mkdir(parents=True)
    (source_root / "runtime.md").write_text(
        "Agent Runtime provides a durable context boundary.\n",
        encoding="utf-8",
    )
    knowledge = KnowledgeService(KnowledgeStore(tmp_path))
    project_id = knowledge.scan("raw/sources", title="Runtime Knowledge")["project"]["id"]
    knowledge.extract(
        project_id,
        "runtime.md",
        pages=[
            {
                "type": "concept",
                "title": "Agent Runtime",
                "slug": "agent-runtime",
                "body": "Agent Runtime provides a durable context boundary.",
            }
        ],
    )
    knowledge.compile(project_id)

    documents = DocumentService(tmp_path)
    writing_project = documents.create_project("Writing task")
    document = documents.create_document(writing_project.id, "Draft")
    chapter = documents.create_chapter(document, "Introduction", content="Old opening.\n")
    sessions = SessionManager(tmp_path)
    search = KnowledgeSearchTool(tmp_path.as_posix(), sessions)
    changeset_tool = WritingChangeSetTool(tmp_path.as_posix(), sessions)
    request = RequestContext(
        channel="websocket",
        chat_id="bridge",
        session_key="websocket:bridge",
        workspace=tmp_path,
        metadata={"knowledge_project_id": project_id},
    )
    scope = build_workspace_scope(tmp_path, "restricted", execution_policy="auto")
    token = bind_workspace_scope(scope)
    try:
        with request_context(request):
            search_result = await search.execute("Agent Runtime", project_id=project_id)
            search_payload = json.loads(str(search_result))
            assert search_payload["citation_count"] == 1
            assert search_payload["citations"][0]["project_id"] == project_id

            session = sessions.get_or_create("websocket:bridge")
            session.metadata["writing_context"] = {
                "project_id": writing_project.id,
                "document_id": document.id,
                "chapter_id": chapter.id,
            }
            sessions.save(session)
            context_block = await WritingContextProvider(WritingStore(tmp_path), sessions)(request)
            assert context_block is not None
            assert f"Selected Knowledge Project: {project_id}" in context_block.content
            assert "Recent bounded Knowledge citations available to the next ChangeSet: 1" in context_block.content
            proposal_result = await changeset_tool.execute(
                action="propose",
                project_id=writing_project.id,
                document_id=document.id,
                chapter_id=chapter.id,
                proposed_content="A durable context boundary is introduced.\n",
                reason="Ground the introduction in the selected knowledge source.",
            )
    finally:
        reset_workspace_scope(token)

    payload = json.loads(str(proposal_result))
    assert payload["knowledge_citations_used"] == 1
    saved = payload["changeset"]["sources"]
    assert saved[0]["project_id"] == project_id
    assert saved[0]["path"].endswith("raw/sources/runtime.md")
    assert WritingStore(tmp_path).read_chapter(document, chapter.id) == "A durable context boundary is introduced.\n"
