from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.channels.websocket.tests.test_websocket_channel import _ch
from nanobot.knowledge.context import KNOWLEDGE_PROJECT_ID_METADATA


@pytest.mark.asyncio
async def test_webui_knowledge_selection_reaches_inbound_metadata() -> None:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    channel = _ch(bus)
    connection = MagicMock()
    channel._webui_connections.add(connection)

    await channel._dispatch_envelope(
        connection,
        "webui-client",
        {
            "type": "message",
            "chat_id": "chat",
            "content": "Use the selected wiki.",
            "knowledge_project_id": "kb_selected",
            "webui": True,
        },
    )

    message = bus.publish_inbound.await_args.args[0]
    assert message.metadata[KNOWLEDGE_PROJECT_ID_METADATA] == "kb_selected"
