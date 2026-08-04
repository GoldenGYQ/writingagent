from __future__ import annotations

import json

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.writing import WritingChangeSetTool
from nanobot.bus.runtime_events import RuntimeEventBus
from nanobot.security.workspace_access import (
    bind_workspace_scope,
    build_workspace_scope,
    reset_workspace_scope,
)
from nanobot.session.interaction_state import pending_interaction
from nanobot.session.manager import SessionManager
from nanobot.writing.changeset import ChangeSetService
from nanobot.writing.context import writing_runtime_snapshot
from nanobot.writing.document import DocumentService
from nanobot.writing.review import ReviewService
from nanobot.writing.revision import RevisionConflict, RevisionService
from nanobot.writing.store import WritingStore


def test_document_runtime_creates_semantic_chapter_and_atomic_assets(tmp_path):
    service = DocumentService(tmp_path)
    project = service.create_project(
        "Agent Runtime 综述",
        goal="形成一篇结构清晰的技术综述",
        style="严谨、克制",
    )
    document = service.create_document(project.id, "Agent Runtime Architecture")
    chapter = service.create_chapter(document, "引言", content="# 引言\n\n第一版。")

    store = WritingStore(tmp_path)
    loaded_project = store.get_project(project.id)
    loaded_document = store.get_document(project.id, document.id)
    loaded_chapter, content = service.read_chapter(loaded_document, chapter.id)

    assert loaded_project.document_ids == [document.id]
    assert loaded_document.artifact_id == document.artifact_id
    assert loaded_chapter.title == "引言"
    assert content == "# 引言\n\n第一版。"
    assert store.get_artifact(project.id, document.id).id == document.artifact_id
    updated = service.update_document(project.id, document.id, title="Renamed", status="review")
    assert updated.title == "Renamed"
    assert store.get_artifact(project.id, document.id).title == "Renamed"


def test_changeset_approval_creates_revision_and_reject_is_non_mutating(tmp_path):
    service = DocumentService(tmp_path)
    project = service.create_project("Test")
    document = service.create_document(project.id, "Doc")
    chapter = service.create_chapter(document, "Chapter 1", content="old\n")
    changesets = ChangeSetService(tmp_path)

    proposal = changesets.propose(
        document,
        chapter,
        "new\n",
        reason="Improve the opening",
        impact="Chapter 1",
        sources=[{"path": "notes.md", "start_line": 1}],
    )
    assert proposal.status == "review"
    assert proposal.changes[0].added == 1
    assert proposal.changes[0].deleted == 1
    assert WritingStore(tmp_path).read_chapter(document, chapter.id) == "old\n"

    rejected = changesets.reject(project.id, proposal.id, feedback="保留例子，但改成更正式的表述")
    assert rejected.status == "rejected"
    assert rejected.feedback == "保留例子，但改成更正式的表述"
    review_history = WritingStore(tmp_path).list_reviews(project.id, document_id=document.id)
    assert review_history[-1].decision == "rejected"
    assert review_history[-1].changeset_id == proposal.id
    assert review_history[-1].suggestion == rejected.feedback
    assert WritingStore(tmp_path).read_chapter(document, chapter.id) == "old\n"

    second = changesets.propose(document, chapter, "new\n", reason="Apply the accepted draft")
    applied = changesets.approve(document, second, author="user")
    assert applied.status == "applied"
    assert applied.applied_revision_id
    assert WritingStore(tmp_path).read_chapter(document, chapter.id) == "new\n"

    revision = WritingStore(tmp_path).get_revision(project.id, applied.applied_revision_id)
    assert revision.number == 1
    assert revision.author == "user"
    assert document.current_revision_id == revision.id
    assert chapter.current_revision_id == revision.id
    assert WritingStore(tmp_path).get_artifact(project.id, document.id).current_revision_id == revision.id
    comparison = RevisionService(WritingStore(tmp_path)).compare(
        project.id,
        revision.id,
        revision.id,
    )
    assert comparison.added == 0
    assert comparison.deleted == 0


def test_changeset_rejects_stale_base_revision(tmp_path):
    service = DocumentService(tmp_path)
    project = service.create_project("Test")
    document = service.create_document(project.id, "Doc")
    chapter = service.create_chapter(document, "Chapter 1", content="one\n")
    changesets = ChangeSetService(tmp_path)

    first = changesets.propose(document, chapter, "two\n", reason="First")
    changesets.approve(document, first)
    with pytest.raises(RevisionConflict):
        changesets.propose(
            document,
            chapter,
            "three\n",
            reason="Stale proposal",
            base_revision_id="rev_stale",
        )


def test_review_issue_is_structured_and_updateable(tmp_path):
    service = DocumentService(tmp_path)
    project = service.create_project("Test")
    document = service.create_document(project.id, "Doc")
    chapter = service.create_chapter(document, "Chapter 1")
    reviews = ReviewService(tmp_path)

    issue = reviews.create_issue(
        project.id,
        document_id=document.id,
        chapter_id=chapter.id,
        kind="citation",
        severity="high",
        description="Missing evidence",
        suggestion="Add a source citation",
        start_line=3,
        end_line=4,
    )
    assert issue.status == "open"
    fixed = reviews.update_status(project.id, issue.id, "fixed")
    assert fixed.status == "fixed"
    assert reviews.list(project.id, document_id=document.id)[0].id == issue.id


def test_runtime_snapshot_is_bounded_to_current_document_context(tmp_path):
    service = DocumentService(tmp_path)
    project = service.create_project("Test")
    document = service.create_document(project.id, "Doc")
    chapter = service.create_chapter(document, "Chapter 1", content="one\n")
    reviews = ReviewService(tmp_path)
    reviews.create_issue(
        project.id,
        document_id=document.id,
        chapter_id=chapter.id,
        description="Check citation",
    )

    snapshot = writing_runtime_snapshot(
        WritingStore(tmp_path),
        {
            "project_id": project.id,
            "document_id": document.id,
            "chapter_id": chapter.id,
        },
    )

    assert snapshot["active"] is True
    assert snapshot["document"]["id"] == document.id
    assert snapshot["chapter"]["id"] == chapter.id
    assert len(snapshot["open_reviews"]) == 1
    assert "content" not in snapshot["chapter"]


@pytest.mark.asyncio
async def test_changeset_proposal_creates_structured_approval_request(tmp_path):
    documents = DocumentService(tmp_path)
    project = documents.create_project("Test")
    document = documents.create_document(project.id, "Doc")
    chapter = documents.create_chapter(document, "Chapter 1", content="old\n")
    sessions = SessionManager(tmp_path)
    runtime_events = RuntimeEventBus()
    events = []
    runtime_events.subscribe(lambda event: events.append(event))
    tool = WritingChangeSetTool(str(tmp_path), sessions, runtime_events)

    with request_context(
        RequestContext(
            channel="websocket",
            chat_id="chat-1",
            session_key="websocket:chat-1",
        )
    ):
        result = await tool.execute(
            action="propose",
            project_id=project.id,
            document_id=document.id,
            chapter_id=chapter.id,
            proposed_content="new\n",
            reason="Improve the opening",
        )

    assert result.is_error
    pending = pending_interaction(sessions.get_or_create("websocket:chat-1").metadata)
    assert pending is not None
    assert pending["kind"] == "change_approval"
    assert pending["fields"][0]["id"] == "feedback"
    assert pending["change"]["files"][0]["diff"]["text"]
    assert events and type(events[-1]).__name__ == "InteractionStateChanged"


@pytest.mark.asyncio
async def test_changeset_auto_policy_applies_without_interaction(tmp_path):
    documents = DocumentService(tmp_path)
    project = documents.create_project("Test")
    document = documents.create_document(project.id, "Doc")
    chapter = documents.create_chapter(document, "Chapter 1", content="old\n")
    sessions = SessionManager(tmp_path)
    tool = WritingChangeSetTool(str(tmp_path), sessions)
    scope = build_workspace_scope(tmp_path, "restricted", execution_policy="auto")
    token = bind_workspace_scope(scope)
    try:
        with request_context(
            RequestContext(
                channel="websocket",
                chat_id="chat-auto",
                session_key="websocket:chat-auto",
            )
        ):
            result = await tool.execute(
                action="propose",
                project_id=project.id,
                document_id=document.id,
                chapter_id=chapter.id,
                proposed_content="new\n",
                reason="Apply the approved editing policy",
            )
    finally:
        reset_workspace_scope(token)

    payload = json.loads(str(result))
    assert payload["execution_policy"] == "auto"
    assert payload["changeset"]["status"] == "applied"
    assert payload["revision"]["id"]
    assert WritingStore(tmp_path).read_chapter(document, chapter.id) == "new\n"
    assert pending_interaction(sessions.get_or_create("websocket:chat-auto").metadata) is None
