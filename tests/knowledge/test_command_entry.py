from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nanobot.bus.events import InboundMessage
from nanobot.command.builtin import cmd_knowledge
from nanobot.command.router import CommandContext


@pytest.mark.asyncio
async def test_knowledge_command_marks_task_boundary_without_running_hidden_workflow() -> None:
    message = InboundMessage(
        channel="websocket",
        sender_id="user",
        chat_id="chat",
        content="/knowledge raw/sources",
    )
    context = CommandContext(
        msg=message,
        session=MagicMock(),
        key=message.session_key,
        raw=message.content,
        args="raw/sources",
        loop=MagicMock(),
        is_user_turn=True,
    )

    result = await cmd_knowledge(context)

    assert result is None
    assert context.msg.content == "/knowledge raw/sources"
    assert context.msg.metadata["knowledge_requested"] == "raw/sources"
    assert context.msg.metadata["original_command"] == "/knowledge"
