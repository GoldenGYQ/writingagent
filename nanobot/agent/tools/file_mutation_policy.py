"""Server-owned execution policy for structured file mutation tools."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from nanobot.agent.tools.base import ToolResult
from nanobot.agent.tools.context import RequestContext, current_request_context
from nanobot.bus.runtime_events import InteractionStateChanged, RuntimeEventBus, RuntimeEventContext
from nanobot.security.workspace_access import current_workspace_scope
from nanobot.session.interaction_state import (
    INTERACTION_STATE_KEY,
    parse_interaction,
    pending_interaction,
)
from nanobot.session.manager import SessionManager
from nanobot.session.working_plan import WORKING_PLAN_KEY, parse_working_plan
from nanobot.utils.file_edit_events import build_unified_diff_payload, line_diff_stats


@dataclass(frozen=True, slots=True)
class PlannedFileWrite:
    path: Path
    before: bytes | None
    after: bytes


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _hash_bytes(data: bytes | None) -> str:
    if data is None:
        return "missing"
    return hashlib.sha256(data).hexdigest()


def _fingerprint(tool_name: str, arguments: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"tool": tool_name, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _display_path(path: Path, workspace: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(workspace.resolve(strict=False)).as_posix()
    except ValueError:
        return path.as_posix()


def _public_files(writes: list[PlannedFileWrite], workspace: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for write in writes:
        before_text: str | None = None
        after_text: str | None = None
        try:
            before_text = "" if write.before is None else write.before.decode("utf-8")
            after_text = write.after.decode("utf-8")
        except UnicodeDecodeError:
            pass
        added = deleted = 0
        diff = None
        if before_text is not None and after_text is not None:
            added, deleted = line_diff_stats(before_text, after_text)
            display = _display_path(write.path, workspace)
            diff = build_unified_diff_payload(
                before_text,
                after_text,
                fromfile=display,
                tofile=display,
            )
        files.append({
            "path": _display_path(write.path, workspace),
            "absolute_path": str(write.path),
            "operation": "create" if write.before is None else "update",
            "added": added,
            "deleted": deleted,
            "binary": before_text is None or after_text is None,
            **({"diff": diff} if diff is not None else {}),
        })
    return files


class FileMutationPolicyGate:
    """Apply read-only/ask/auto policy before a structured file tool writes."""

    def __init__(
        self,
        sessions: SessionManager | None,
        runtime_events: RuntimeEventBus | None,
    ) -> None:
        self._sessions = sessions
        self._runtime_events = runtime_events

    async def authorize(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        writes: list[PlannedFileWrite],
    ) -> ToolResult | None:
        scope = current_workspace_scope()
        policy = scope.execution_policy if scope is not None else "auto"
        if policy == "auto":
            return None
        if policy == "read_only":
            return ToolResult.error(
                "Error: File mutation blocked by Read-only execution policy. "
                "Change the execution policy before retrying."
            )

        ctx = current_request_context()
        if self._sessions is None or ctx is None or not ctx.session_key or scope is None:
            return ToolResult.error(
                "Error: Ask before apply requires an active durable session."
            )
        session = self._sessions.get_or_create(ctx.session_key)
        fingerprint = _fingerprint(tool_name, arguments)
        existing = parse_interaction(session.metadata.get(INTERACTION_STATE_KEY))

        if existing and existing.get("kind") == "change_approval":
            server = existing.get("_server")
            server_data = cast(dict[str, Any], server) if isinstance(server, dict) else {}
            same_proposal = server_data.get("fingerprint") == fingerprint
            response = existing.get("response")
            response_data = cast(dict[str, Any], response) if isinstance(response, dict) else {}
            action = response_data.get("action")
            if (
                same_proposal
                and server_data.get("consumed_at")
                and server_data.get("consumed_turn_id") == ctx.turn_id
            ):
                return ToolResult.error("Error: Approved file change has already been consumed.")
            if same_proposal and existing.get("status") == "resolved" and action == "apply_once":
                if not self._before_hashes_match(writes, server_data):
                    existing["status"] = "stale"
                    existing["pending"] = False
                    server_data["stale_at"] = _now()
                    self._sessions.save(session)
                else:
                    server_data["consumed_at"] = _now()
                    server_data["consumed_turn_id"] = ctx.turn_id
                    existing["status"] = "consumed"
                    self._sessions.save(session)
                    return None
            elif same_proposal and existing.get("status") in {"resolved", "cancelled"}:
                handled_turn = server_data.get("rejected_turn_id")
                if handled_turn == ctx.turn_id:
                    return ToolResult.error("Error: User rejected this file change.")
                server_data["rejected_turn_id"] = ctx.turn_id
                self._sessions.save(session)
                return ToolResult.error("Error: User rejected this file change.")

        waiting = pending_interaction(session.metadata)
        if waiting:
            return ToolResult.error(
                f"Error: interaction {waiting.get('id', '')} is already waiting for user input."
            )

        request = self._build_request(
            tool_name=tool_name,
            arguments=arguments,
            writes=writes,
            workspace=scope.project_path,
            fingerprint=fingerprint,
            metadata=session.metadata,
        )
        previous = deepcopy(session.metadata)
        session.metadata[INTERACTION_STATE_KEY] = request
        plan = parse_working_plan(session.metadata.get(WORKING_PLAN_KEY))
        if plan and plan.get("status") in {"draft", "active"}:
            updated_plan = dict(plan)
            updated_plan["status"] = "waiting_for_user"
            updated_plan["version"] = int(plan.get("version", 1)) + 1
            updated_plan["updated_at"] = _now()
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
        await self._publish(ctx, session.metadata)
        return ToolResult.error(
            f"Approval required for proposed file change {request['id']}. "
            "After the user approves, replay this exact tool call once."
        )

    @staticmethod
    def _before_hashes_match(
        writes: list[PlannedFileWrite],
        server_data: dict[str, Any],
    ) -> bool:
        raw_hashes = server_data.get("before_hashes")
        if not isinstance(raw_hashes, dict):
            return False
        before_hashes = cast(dict[str, Any], raw_hashes)
        for write in writes:
            current = write.path.read_bytes() if write.path.exists() else None
            if before_hashes.get(str(write.path)) != _hash_bytes(current):
                return False
        return True

    @staticmethod
    def _build_request(
        *,
        tool_name: str,
        arguments: dict[str, Any],
        writes: list[PlannedFileWrite],
        workspace: Path,
        fingerprint: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        files = _public_files(writes, workspace)
        added = sum(int(item.get("added", 0)) for item in files)
        deleted = sum(int(item.get("deleted", 0)) for item in files)
        plan = parse_working_plan(metadata.get(WORKING_PLAN_KEY))
        return {
            "id": f"interaction_{uuid4().hex}",
            "pending": True,
            "status": "pending",
            "kind": "change_approval",
            "reason": "file_change_approval",
            "title": "Review proposed file changes",
            "prompt": f"{tool_name} wants to change {len(files)} file(s) (+{added}/-{deleted}).",
            "fields": [],
            "actions": [
                {"id": "apply_once", "label": "Apply once", "style": "primary"},
                {"id": "reject", "label": "Reject", "style": "danger"},
            ],
            "change": {
                "tool": tool_name,
                "files": files,
                "added": added,
                "deleted": deleted,
            },
            "created_at": _now(),
            "plan_ref": (
                {"id": plan.get("id"), "version": plan.get("version")}
                if plan else None
            ),
            "_server": {
                "fingerprint": fingerprint,
                "arguments": arguments,
                "before_hashes": {
                    str(write.path): _hash_bytes(write.before) for write in writes
                },
            },
        }

    async def _publish(self, ctx: RequestContext, metadata: dict[str, Any]) -> None:
        if self._runtime_events is None or not ctx.session_key:
            return
        await self._runtime_events.publish(InteractionStateChanged(
            context=RuntimeEventContext(
                channel=ctx.channel,
                chat_id=ctx.chat_id,
                session_key=ctx.session_key,
                metadata=dict(ctx.metadata or {}),
            ),
            session_metadata=dict(metadata),
        ))
