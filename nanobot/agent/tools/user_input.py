"""Structured human-in-the-loop input request tool."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import ToolContext, current_request_context
from nanobot.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.bus.events import OUTBOUND_META_AGENT_UI, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.bus.runtime_events import InteractionStateChanged, RuntimeEventBus, RuntimeEventContext
from nanobot.session.interaction_state import INTERACTION_STATE_KEY, pending_interaction
from nanobot.session.working_plan import WORKING_PLAN_KEY, parse_working_plan

_FIELD_TYPES = ("text", "textarea", "select", "radio", "checkbox", "confirm")


def _now() -> str:
    return datetime.now().astimezone().isoformat()


_OPTION_SCHEMA = ObjectSchema(
    {
        "value": StringSchema("Stable submitted value.", min_length=1, max_length=200),
        "label": StringSchema("User-visible option label.", min_length=1, max_length=240),
        "description": StringSchema("Optional explanation.", max_length=500, nullable=True),
    },
    required=["value", "label"],
    additional_properties=False,
)
_FIELD_SCHEMA = ObjectSchema(
    {
        "id": StringSchema("Stable field identifier.", min_length=1, max_length=80),
        "type": StringSchema("Input control type.", enum=_FIELD_TYPES),
        "label": StringSchema("User-visible field label.", min_length=1, max_length=240),
        "description": StringSchema("Optional field help.", max_length=1000, nullable=True),
        "required": BooleanSchema(description="Whether the user must provide a value."),
        "options": ArraySchema(
            _OPTION_SCHEMA,
            description="Choices for select/radio/checkbox.",
            max_items=20,
            nullable=True,
        ),
    },
    required=["id", "type", "label", "required"],
    additional_properties=False,
)
_ACTION_SCHEMA = ObjectSchema(
    id=StringSchema("Stable action identifier.", min_length=1, max_length=80),
    label=StringSchema("User-visible button label.", min_length=1, max_length=120),
    style=StringSchema("Visual intent.", enum=("primary", "secondary", "danger")),
    required=["id", "label", "style"],
    additional_properties=False,
)


@tool_parameters(tool_parameters_schema(
    title=StringSchema("Form title.", min_length=1, max_length=240),
    prompt=StringSchema("Why input is required and what happens next.", min_length=1, max_length=4000),
    reason=StringSchema("Machine-readable interaction reason.", min_length=1, max_length=100),
    fields=ArraySchema(_FIELD_SCHEMA, description="One or more form fields.", min_items=1, max_items=12),
    actions=ArraySchema(_ACTION_SCHEMA, description="Form actions.", min_items=1, max_items=4),
    required=["title", "prompt", "reason", "fields", "actions"],
))
class RequestUserInputTool(Tool):
    """Pause execution and request structured user input."""

    def __init__(
        self,
        sessions: Any,
        bus: MessageBus | None = None,
        runtime_events: RuntimeEventBus | None = None,
    ) -> None:
        self._sessions = sessions
        self._bus = bus
        self._runtime_events = runtime_events

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("RequestUserInputTool requires sessions")
        return cls(ctx.sessions, ctx.bus, ctx.runtime_events)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "request_user_input"

    @property
    def description(self) -> str:
        return (
            "Pause the current run and show a durable structured form. Use whenever execution "
            "must wait for confirmation, approval, missing information, or an essential choice. "
            "After this succeeds, stop working until the user responds."
        )

    @property
    def exclusive(self) -> bool:
        return True

    async def execute(
        self,
        title: str,
        prompt: str,
        reason: str,
        fields: list[dict[str, Any]],
        actions: list[dict[str, Any]],
        **_: Any,
    ) -> str:
        ctx = current_request_context()
        if not ctx or not ctx.session_key:
            return ToolResult.error("Error: request_user_input requires an active session.")
        session = self._sessions.get_or_create(ctx.session_key)
        current = pending_interaction(session.metadata)
        if current:
            return ToolResult.error(
                f"Error: interaction {current.get('id', '')} is already waiting for user input."
            )
        now = _now()
        plan = parse_working_plan(session.metadata.get(WORKING_PLAN_KEY))
        request = {
            "id": f"interaction_{uuid4().hex}",
            "pending": True,
            "status": "pending",
            "kind": "form",
            "reason": reason.strip(),
            "title": title.strip(),
            "prompt": prompt.strip(),
            "fields": [dict(cast(dict[str, Any], field)) for field in fields],
            "actions": [dict(cast(dict[str, Any], action)) for action in actions],
            "created_at": now,
            "plan_ref": (
                {"id": plan.get("id"), "version": plan.get("version")}
                if plan else None
            ),
        }
        previous = deepcopy(session.metadata)
        session.metadata[INTERACTION_STATE_KEY] = request
        if plan and plan.get("status") in {"draft", "active"}:
            updated_plan = dict(plan)
            updated_plan["status"] = "waiting_for_user"
            updated_plan["version"] = int(plan.get("version", 1)) + 1
            updated_plan["updated_at"] = now
            session.metadata[WORKING_PLAN_KEY] = updated_plan
            request["plan_ref"] = {
                "id": updated_plan.get("id"),
                "version": updated_plan.get("version"),
            }
        try:
            self._sessions.save(session)
        except BaseException:
            session.metadata.clear()
            session.metadata.update(previous)
            raise

        event_ctx = RuntimeEventContext(
            channel=ctx.channel,
            chat_id=ctx.chat_id,
            session_key=ctx.session_key,
            metadata=dict(ctx.metadata or {}),
        )
        if self._runtime_events is not None:
            await self._runtime_events.publish(InteractionStateChanged(
                context=event_ctx,
                session_metadata=dict(session.metadata),
            ))
        if self._bus is not None and ctx.channel != "websocket":
            labels = [[str(action.get("label", "")) for action in actions if action.get("label")]]
            await self._bus.publish_outbound(OutboundMessage(
                channel=ctx.channel,
                chat_id=ctx.chat_id,
                content=f"{title.strip()}\n\n{prompt.strip()}",
                buttons=labels,
                metadata={
                    OUTBOUND_META_AGENT_UI: {"kind": "interaction_request", "data": request},
                },
            ))
        return (
            f"User input request {request['id']} recorded. Execution is now paused. "
            "Do not continue until the host resumes this session with the user's response."
        )
