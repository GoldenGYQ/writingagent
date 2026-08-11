"""Goal-independent working-plan tools."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import RequestContext, ToolContext, current_request_context
from nanobot.agent.tools.schema import (
    ArraySchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.bus.runtime_events import RuntimeEventBus, RuntimeEventContext, WorkingPlanChanged
from nanobot.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines
from nanobot.session.working_plan import (
    WORKING_PLAN_KEY,
    parse_working_plan,
    working_plan_raw,
    working_plan_runtime_lines,
)

_PLAN_STATUSES = ("draft", "active", "waiting_for_user", "completed", "cancelled")
_STEP_STATUSES = ("pending", "in_progress", "completed", "blocked", "skipped")
_PLAN_KINDS = ("general", "writing")


def _now() -> str:
    return datetime.now().astimezone().isoformat()


_STEP_SCHEMA = ObjectSchema(
    {
        "id": StringSchema("Stable step identifier.", min_length=1, max_length=80),
        "title": StringSchema("Short actionable step title.", min_length=1, max_length=240),
        "description": StringSchema("Optional completion detail.", max_length=2000, nullable=True),
        "status": StringSchema("Current step state.", enum=_STEP_STATUSES),
    },
    required=["id", "title", "status"],
    additional_properties=False,
)


class _WorkingPlanMixin:
    def __init__(self, sessions: Any, runtime_events: RuntimeEventBus | None = None) -> None:
        self._sessions = sessions
        self._runtime_events = runtime_events

    def _session(self):
        ctx = current_request_context()
        return self._sessions.get_or_create(ctx.session_key) if ctx and ctx.session_key else None

    def _save(self, session: Any, plan: dict[str, Any]) -> None:
        before = deepcopy(session.metadata)
        session.metadata[WORKING_PLAN_KEY] = plan
        try:
            self._sessions.save(session)
        except BaseException:
            session.metadata.clear()
            session.metadata.update(before)
            raise

    async def _publish(self, session: Any) -> None:
        ctx = current_request_context()
        if not self._runtime_events or not ctx or not ctx.chat_id or not ctx.session_key:
            return
        await self._runtime_events.publish(WorkingPlanChanged(
            context=RuntimeEventContext(
                channel=ctx.channel,
                chat_id=ctx.chat_id,
                session_key=ctx.session_key,
                metadata=dict(ctx.metadata or {}),
            ),
            session_metadata=dict(session.metadata),
        ))


@tool_parameters(tool_parameters_schema(
    title=StringSchema("Short plan title.", min_length=1, max_length=240),
    objective=StringSchema("Self-contained desired outcome and completion criteria.", min_length=1, max_length=4000),
    kind=StringSchema("Use writing for writing workflows; otherwise general.", enum=_PLAN_KINDS),
    steps=ArraySchema(_STEP_SCHEMA, description="Ordered executable steps.", min_items=1, max_items=50),
    required=["title", "objective", "kind", "steps"],
))
class CreateWorkingPlanTool(Tool, _WorkingPlanMixin):
    """Create or replace the session's current working plan."""

    def __init__(self, sessions: Any, runtime_events: RuntimeEventBus | None = None) -> None:
        _WorkingPlanMixin.__init__(self, sessions, runtime_events)

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("CreateWorkingPlanTool requires sessions")
        return cls(ctx.sessions, ctx.runtime_events)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "create_working_plan"

    @property
    def description(self) -> str:
        return (
            "Create durable step-by-step runtime state for a multi-step task, independently of "
            "whether a sustained goal exists. Use this before substantial execution when the "
            "task has multiple dependent steps; skip it for a trivial one-step answer. Use "
            "kind='writing' for writing workflows, then keep the steps current with "
            "update_working_plan."
        )

    def runtime_context_provider(self):
        return self._runtime_context

    async def _runtime_context(self, request: RequestContext) -> RuntimeContextBlock | None:
        if not request.session_key:
            return None
        session = self._sessions.get_or_create(request.session_key)
        state = wrap_runtime_context_lines(working_plan_runtime_lines(session.metadata))
        if not state:
            return None
        return RuntimeContextBlock(source="working_plan", content=state)

    async def execute(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        title: str,
        objective: str,
        kind: str,
        steps: list[dict[str, Any]],
        **_: Any,
    ) -> str:
        session = self._session()
        if session is None:
            return ToolResult.error("Error: create_working_plan requires an active session.")
        existing = parse_working_plan(working_plan_raw(session.metadata))
        version = int(existing.get("version", 0)) + 1 if existing else 1
        now = _now()
        plan = {
            "id": str(existing.get("id")) if existing and existing.get("id") else f"plan_{uuid4().hex}",
            "version": version,
            "kind": kind,
            "status": "active",
            "title": title.strip(),
            "objective": objective.strip(),
            "steps": [dict(step) for step in steps],
            "created_at": existing.get("created_at", now) if existing else now,
            "updated_at": now,
        }
        self._save(session, plan)
        await self._publish(session)
        return f"Working plan recorded: {plan['id']} version {version}."


@tool_parameters(tool_parameters_schema(
    base_version=StringSchema("Current plan version as a decimal string.", min_length=1, max_length=20),
    status=StringSchema("Overall plan status.", enum=_PLAN_STATUSES),
    title=StringSchema("Updated title, or null to keep it.", max_length=240, nullable=True),
    objective=StringSchema("Updated objective, or null to keep it.", max_length=4000, nullable=True),
    steps=ArraySchema(_STEP_SCHEMA, description="Complete replacement step list.", min_items=1, max_items=50),
    required=["base_version", "status", "steps"],
))
class UpdateWorkingPlanTool(Tool, _WorkingPlanMixin):
    """Version-safe update of the current working plan."""

    def __init__(self, sessions: Any, runtime_events: RuntimeEventBus | None = None) -> None:
        _WorkingPlanMixin.__init__(self, sessions, runtime_events)

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise RuntimeError("UpdateWorkingPlanTool requires sessions")
        return cls(ctx.sessions, ctx.runtime_events)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @property
    def name(self) -> str:
        return "update_working_plan"

    @property
    def description(self) -> str:
        return (
            "Replace the current durable plan snapshot using optimistic version checking. "
            "Call this after a step changes, pass the latest base_version, and provide the "
            "complete ordered step list so the runtime state remains authoritative."
        )

    async def execute(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        base_version: str,
        status: str,
        steps: list[dict[str, Any]],
        title: str | None = None,
        objective: str | None = None,
        **_: Any,
    ) -> str:
        session = self._session()
        if session is None:
            return ToolResult.error("Error: update_working_plan requires an active session.")
        plan = parse_working_plan(working_plan_raw(session.metadata))
        if not plan:
            return ToolResult.error("Error: no working plan exists.")
        try:
            expected = int(base_version)
        except ValueError:
            return ToolResult.error("Error: base_version must be a decimal integer.")
        current = int(plan.get("version", 1))
        if expected != current:
            return ToolResult.error(f"Error: working plan version conflict; current version is {current}.")
        updated = {
            **plan,
            "version": current + 1,
            "status": status,
            "steps": [dict(step) for step in steps],
            "updated_at": _now(),
        }
        if title is not None:
            updated["title"] = title.strip()
        if objective is not None:
            updated["objective"] = objective.strip()
        self._save(session, updated)
        await self._publish(session)
        return f"Working plan updated to version {updated['version']} ({status})."
