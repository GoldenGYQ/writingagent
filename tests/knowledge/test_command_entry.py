from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nanobot.bus.events import InboundMessage
from nanobot.command.builtin import cmd_knowledge
from nanobot.command.router import CommandContext


@pytest.mark.asyncio
async def test_knowledge_command_initializes_task_boundary_without_scanning(tmp_path) -> None:
    message = InboundMessage(
        channel="websocket",
        sender_id="user",
        chat_id="chat",
        content="/knowledge raw/sources",
    )
    (tmp_path / "raw" / "sources").mkdir(parents=True)
    loop = MagicMock()
    loop.workspace = tmp_path
    loop.workspace_scopes = None
    session = MagicMock()
    session.metadata = {}
    context = CommandContext(
        msg=message,
        session=session,
        key=message.session_key,
        raw=message.content,
        args="raw/sources",
        loop=loop,
        is_user_turn=True,
    )

    result = await cmd_knowledge(context)

    assert result is None
    assert context.msg.content == "/knowledge raw/sources"
    assert context.msg.metadata["knowledge_requested"] == "raw/sources"
    assert context.msg.metadata["original_command"] == "/knowledge"
    assert context.msg.metadata["knowledge_project_id"].startswith("kb_")
    assert context.msg.metadata["knowledge_context"]["task_id"].startswith("task_")
    assert context.msg.metadata["goal_requested"] is True
    assert context.msg.metadata["goal_domain"] == "knowledge"
    assert len(context.turn_scopes) == 1
    project_root = tmp_path / "wikis" / context.msg.metadata["knowledge_project_id"]
    assert (project_root / "project.json").exists()
    assert (project_root / "knowledge" / "task.json").exists()
    assert not list((project_root / "raw").rglob("*"))


@pytest.mark.asyncio
async def test_knowledge_command_without_source_enters_runtime_selection_state(tmp_path) -> None:
    message = InboundMessage(
        channel="websocket",
        sender_id="user",
        chat_id="chat",
        content="/knowledge",
    )
    loop = MagicMock()
    loop.workspace = tmp_path
    session = MagicMock()
    session.metadata = {}
    context = CommandContext(
        msg=message,
        session=session,
        key=message.session_key,
        raw=message.content,
        args="",
        loop=loop,
        is_user_turn=True,
    )

    result = await cmd_knowledge(context)

    assert result is None
    assert context.msg.metadata["knowledge_selection_pending"] is True
    assert context.msg.metadata["goal_requested"] is True
    assert len(context.turn_scopes) == 1
    assert not (tmp_path / "wikis").exists()
