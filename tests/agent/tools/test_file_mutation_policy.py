from __future__ import annotations

import pytest

from nanobot.agent.tools.apply_patch import ApplyPatchTool
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.filesystem import EditFileTool, WriteFileTool
from nanobot.security.workspace_access import (
    WorkspaceScopeError,
    bind_workspace_scope,
    build_workspace_scope,
    reset_workspace_scope,
    validate_workspace_scope_payload,
)
from nanobot.session.interaction_state import (
    interaction_ws_blob,
    pending_interaction,
    resolve_interaction,
)
from nanobot.session.manager import SessionManager


def _request_context() -> RequestContext:
    return RequestContext(
        channel="websocket",
        chat_id="chat-1",
        session_key="websocket:chat-1",
        metadata={"webui": True},
        turn_id="turn-1",
    )


def test_workspace_scope_validates_execution_policy(tmp_path):
    scope = validate_workspace_scope_payload(
        {
            "project_path": str(tmp_path),
            "access_mode": "restricted",
            "execution_policy": "ask",
        },
        default_workspace=tmp_path,
        default_restrict_to_workspace=True,
        source_channel="websocket",
    )
    assert scope.execution_policy == "ask"
    assert scope.payload()["execution_policy"] == "ask"
    with pytest.raises(WorkspaceScopeError, match="execution_policy"):
        validate_workspace_scope_payload(
            {
                "project_path": str(tmp_path),
                "access_mode": "restricted",
                "execution_policy": "untrusted",
            },
            default_workspace=tmp_path,
            default_restrict_to_workspace=True,
        )


async def _execute(tool: Tool, scope, **kwargs):
    token = bind_workspace_scope(scope)
    try:
        with request_context(_request_context()):
            return await tool.execute(**kwargs)
    finally:
        reset_workspace_scope(token)


@pytest.mark.asyncio
async def test_read_only_blocks_write_before_file_creation(tmp_path):
    sessions = SessionManager(tmp_path)
    tool = WriteFileTool(workspace=tmp_path, sessions=sessions)
    scope = build_workspace_scope(
        tmp_path,
        "restricted",
        execution_policy="read_only",
        source_channel="websocket",
    )

    result = await _execute(tool, scope, path="draft.md", content="draft")

    assert getattr(result, "is_error", False) is True
    assert "Read-only" in result
    assert not (tmp_path / "draft.md").exists()


@pytest.mark.asyncio
async def test_ask_creates_private_durable_proposal_then_applies_once(tmp_path):
    sessions = SessionManager(tmp_path)
    tool = WriteFileTool(workspace=tmp_path, sessions=sessions)
    scope = build_workspace_scope(
        tmp_path,
        "restricted",
        execution_policy="ask",
        source_channel="websocket",
    )

    result = await _execute(tool, scope, path="draft.md", content="hello\n")

    assert getattr(result, "is_error", False) is True
    assert "Approval required" in result
    assert not (tmp_path / "draft.md").exists()
    session = sessions.get_or_create("websocket:chat-1")
    pending = pending_interaction(session.metadata)
    assert pending and pending["kind"] == "change_approval"
    assert pending["change"]["files"][0]["path"] == "draft.md"
    assert "_server" in pending
    assert "_server" not in interaction_ws_blob(session.metadata)

    resolve_interaction(
        session.metadata,
        interaction_id=str(pending["id"]),
        action="apply_once",
        values={},
    )
    sessions.save(session)
    applied = await _execute(tool, scope, path="draft.md", content="hello\n")

    assert "Successfully wrote" in applied
    assert (tmp_path / "draft.md").read_text(encoding="utf-8") == "hello\n"
    replay = await _execute(tool, scope, path="draft.md", content="hello\n")
    assert getattr(replay, "is_error", False) is True
    assert "already been consumed" in replay


@pytest.mark.asyncio
async def test_ask_reproposes_when_file_changed_after_approval(tmp_path):
    target = tmp_path / "draft.md"
    target.write_text("v1\n", encoding="utf-8")
    sessions = SessionManager(tmp_path)
    tool = WriteFileTool(workspace=tmp_path, sessions=sessions)
    scope = build_workspace_scope(
        tmp_path,
        "restricted",
        execution_policy="ask",
        source_channel="websocket",
    )

    await _execute(tool, scope, path="draft.md", content="v2\n")
    session = sessions.get_or_create("websocket:chat-1")
    first = pending_interaction(session.metadata)
    assert first
    resolve_interaction(
        session.metadata,
        interaction_id=str(first["id"]),
        action="apply_once",
        values={},
    )
    sessions.save(session)
    target.write_text("external\n", encoding="utf-8")

    result = await _execute(tool, scope, path="draft.md", content="v2\n")

    assert getattr(result, "is_error", False) is True
    second = pending_interaction(session.metadata)
    assert second and second["id"] != first["id"]
    assert target.read_text(encoding="utf-8") == "external\n"


@pytest.mark.asyncio
async def test_rejected_proposal_cannot_write(tmp_path):
    sessions = SessionManager(tmp_path)
    tool = WriteFileTool(workspace=tmp_path, sessions=sessions)
    scope = build_workspace_scope(
        tmp_path,
        "restricted",
        execution_policy="ask",
        source_channel="websocket",
    )
    await _execute(tool, scope, path="draft.md", content="blocked\n")
    session = sessions.get_or_create("websocket:chat-1")
    pending = pending_interaction(session.metadata)
    assert pending
    resolve_interaction(
        session.metadata,
        interaction_id=str(pending["id"]),
        action="reject",
        values={},
    )
    sessions.save(session)

    result = await _execute(tool, scope, path="draft.md", content="blocked\n")

    assert getattr(result, "is_error", False) is True
    assert "rejected" in result
    assert not (tmp_path / "draft.md").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_kind", ["edit", "patch"])
async def test_ask_previews_edit_tools_without_writing_before_approval(tmp_path, tool_kind):
    target = tmp_path / "draft.md"
    target.write_text("before\n", encoding="utf-8")
    sessions = SessionManager(tmp_path)
    scope = build_workspace_scope(
        tmp_path,
        "restricted",
        execution_policy="ask",
        source_channel="websocket",
    )
    if tool_kind == "edit":
        tool = EditFileTool(workspace=tmp_path, sessions=sessions)
        arguments = {"path": "draft.md", "old_text": "before", "new_text": "after"}
    else:
        tool = ApplyPatchTool(workspace=tmp_path, sessions=sessions)
        arguments = {
            "edits": [{
                "path": "draft.md",
                "action": "replace",
                "old_text": "before",
                "new_text": "after",
            }],
        }

    result = await _execute(tool, scope, **arguments)

    assert getattr(result, "is_error", False) is True
    assert target.read_text(encoding="utf-8") == "before\n"
    session = sessions.get_or_create("websocket:chat-1")
    pending = pending_interaction(session.metadata)
    assert pending
    assert pending["change"]["files"][0]["diff"]["text"]
