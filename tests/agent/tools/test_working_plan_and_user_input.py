from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.runner_helpers import make_run_spec
from nanobot.agent.runner import AgentRunner
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.user_input import RequestUserInputTool
from nanobot.agent.tools.working_plan import CreateWorkingPlanTool, UpdateWorkingPlanTool
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.runtime_context import RuntimeContextBlock
from nanobot.session.interaction_state import pending_interaction, resolve_interaction
from nanobot.session.manager import SessionManager
from nanobot.session.working_plan import active_working_plan


def _ctx() -> RequestContext:
    return RequestContext(
        channel="websocket",
        chat_id="chat-1",
        session_key="websocket:chat-1",
        metadata={"webui": True},
    )


def _steps(status: str = "pending") -> list[dict[str, str]]:
    return [{"id": "research", "title": "Research", "status": status}]


@pytest.mark.asyncio
async def test_working_plan_exists_without_sustained_goal(tmp_path):
    sessions = SessionManager(tmp_path)
    create = CreateWorkingPlanTool(sessions)
    update = UpdateWorkingPlanTool(sessions)
    with request_context(_ctx()):
        result = await create.execute(
            title="Article plan",
            objective="Produce an approved article",
            kind="writing",
            steps=_steps(),
        )
    assert "Working plan recorded" in result
    session = sessions.get_or_create("websocket:chat-1")
    assert "goal_state" not in session.metadata
    plan = active_working_plan(session.metadata)
    assert plan and plan["kind"] == "writing" and plan["version"] == 1

    with request_context(_ctx()):
        result = await update.execute(
            base_version="1",
            status="active",
            steps=_steps("completed"),
        )
    assert "version 2" in result
    assert session.metadata["working_plan"]["steps"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_working_plan_runtime_context_is_conditional_plan_state(tmp_path):
    sessions = SessionManager(tmp_path)
    tool = CreateWorkingPlanTool(sessions)
    provider = tool.runtime_context_provider()

    assert provider is not None
    assert "skip it for a trivial one-step answer" in tool.description

    # Static usage guidance belongs to the tool schema, not every user turn.
    assert await provider(_ctx()) is None

    with request_context(_ctx()):
        await tool.execute(
            title="Article plan",
            objective="Produce an approved article",
            kind="writing",
            steps=_steps(),
        )

    block = await provider(_ctx())
    assert isinstance(block, RuntimeContextBlock)
    assert block.source == "working_plan"
    assert "[Working Plan Guidance]" not in block.content
    assert "Working plan (durable runtime state):" in block.content
    assert "Plan ID:" in block.content
    assert "[Runtime Context" in block.content


@pytest.mark.asyncio
async def test_request_user_input_pauses_plan_and_resolves(tmp_path):
    sessions = SessionManager(tmp_path)
    plan_tool = CreateWorkingPlanTool(sessions)
    request_tool = RequestUserInputTool(sessions, MessageBus())
    with request_context(_ctx()):
        await plan_tool.execute(
            title="Review plan",
            objective="Wait for approval",
            kind="writing",
            steps=_steps(),
        )
        result = await request_tool.execute(
            title="Approve plan",
            prompt="Choose a citation style.",
            reason="plan_approval",
            fields=[{
                "id": "citation_style",
                "type": "select",
                "label": "Citation style",
                "required": True,
                "options": [{"value": "apa", "label": "APA"}],
            }],
            actions=[{"id": "approve", "label": "Approve", "style": "primary"}],
        )
    assert "Execution is now paused" in result
    session = sessions.get_or_create("websocket:chat-1")
    pending = pending_interaction(session.metadata)
    assert pending and session.metadata["working_plan"]["status"] == "waiting_for_user"

    with pytest.raises(ValueError, match="missing required field"):
        resolve_interaction(
            session.metadata,
            interaction_id=str(pending["id"]),
            action="approve",
            values={},
        )

    with pytest.raises(ValueError, match="invalid value for field"):
        resolve_interaction(
            session.metadata,
            interaction_id=str(pending["id"]),
            action="approve",
            values={"citation_style": "invented"},
        )

    resolved = resolve_interaction(
        session.metadata,
        interaction_id=str(pending["id"]),
        action="approve",
        values={"citation_style": "apa"},
    )
    assert resolved["status"] == "resolved"
    assert pending_interaction(session.metadata) is None
    assert session.metadata["working_plan"]["status"] == "active"


@pytest.mark.asyncio
async def test_evidence_request_allows_natural_attachment_response(tmp_path):
    sessions = SessionManager(tmp_path)
    request_tool = RequestUserInputTool(sessions)
    with request_context(_ctx()):
        await request_tool.execute(
            title="补充资质证据",
            prompt="请上传证书或说明缺失原因。",
            reason="knowledge_gap",
            fields=[],
            actions=[{"id": "submit", "label": "提交材料", "style": "primary"}],
            allow_message_response=True,
            accepts_attachments=True,
            response_scope="knowledge_candidate",
        )

    pending = pending_interaction(
        sessions.get_or_create("websocket:chat-1").metadata
    )
    assert pending is not None
    assert pending["allow_message_response"] is True
    assert pending["accepts_attachments"] is True
    assert pending["response_scope"] == "knowledge_candidate"


@pytest.mark.asyncio
async def test_runner_stops_immediately_after_input_request(tmp_path):
    sessions = SessionManager(tmp_path)
    request_tool = RequestUserInputTool(sessions)
    registry = ToolRegistry()
    registry.register(request_tool)
    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=[ToolCallRequest(
            id="call-1",
            name="request_user_input",
            arguments={
                "title": "Confirm",
                "prompt": "Continue?",
                "reason": "confirmation",
                "fields": [{
                    "id": "approved",
                    "type": "confirm",
                    "label": "Approve",
                    "required": True,
                }],
                "actions": [{"id": "approve", "label": "Approve", "style": "primary"}],
            },
        )],
        usage={},
    ))
    session = sessions.get_or_create("websocket:chat-1")
    spec = make_run_spec(
        provider,
        initial_messages=[{"role": "user", "content": "Make a plan"}],
        tools=registry,
        model="test-model",
        max_iterations=3,
        max_tool_result_chars=10_000,
        wait_for_user_predicate=lambda: pending_interaction(session.metadata) is not None,
    )
    with request_context(_ctx()):
        result = await AgentRunner().run(spec)
    assert result.stop_reason == "waiting_for_user"
    assert provider.chat_with_retry.await_count == 1
    assert pending_interaction(session.metadata) is not None
