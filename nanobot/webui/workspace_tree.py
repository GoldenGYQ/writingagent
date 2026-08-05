"""Workspace-scoped directory tree payloads for the WebUI."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nanobot.security.workspace_access import WorkspaceScope
from nanobot.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path

_DEFAULT_DEPTH = 3
_MAX_DEPTH = 6
_DEFAULT_LIMIT = 240
_MAX_LIMIT = 800

# Keep the explorer useful for source documents without expanding dependency
# caches and generated trees by default. Individual files inside these folders
# remain directly previewable when the user opens them by path.
_HIDDEN_DIRECTORY_NAMES = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".pytest_cache",
})


class WorkspaceTreeError(ValueError):
    """Raised when a workspace tree request is invalid or unavailable."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def workspace_tree_payload(
    raw_path: str | None,
    *,
    scope: WorkspaceScope,
    depth: int = _DEFAULT_DEPTH,
    limit: int = _DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Return a bounded, workspace-rooted directory tree."""

    root = _resolve_tree_path(raw_path, scope=scope)
    if not root.is_dir():
        raise WorkspaceTreeError(404, "workspace directory not found")

    bounded_depth = _bounded_int(depth, minimum=0, maximum=_MAX_DEPTH, fallback=_DEFAULT_DEPTH)
    bounded_limit = _bounded_int(limit, minimum=1, maximum=_MAX_LIMIT, fallback=_DEFAULT_LIMIT)
    state = _TreeState(limit=bounded_limit)
    node = _tree_node(root, scope.project_path, depth=bounded_depth, state=state)
    return {
        "root": str(scope.project_path),
        "path": _relative_path(root, scope.project_path),
        "depth": bounded_depth,
        "limit": bounded_limit,
        "truncated": state.truncated,
        "entries": node.get("children", []),
    }


class _TreeState:
    def __init__(self, *, limit: int) -> None:
        self.limit = limit
        self.seen = 0
        self.truncated = False


def _tree_node(path: Path, workspace: Path, *, depth: int, state: _TreeState) -> dict[str, Any]:
    node = _entry_payload(path, workspace)
    if not path.is_dir() or depth <= 0:
        return node

    children: list[dict[str, Any]] = []
    try:
        entries = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold()))
    except OSError:
        node["unreadable"] = True
        return node

    for child in entries:
        if state.seen >= state.limit:
            state.truncated = True
            break
        if child.is_dir() and child.name in _HIDDEN_DIRECTORY_NAMES:
            continue
        state.seen += 1
        children.append(_tree_node(child, workspace, depth=depth - 1, state=state))
    node["children"] = children
    node["has_children"] = bool(children) or any(
        child.is_dir() and child.name not in _HIDDEN_DIRECTORY_NAMES for child in entries
    )
    return node


def _entry_payload(path: Path, workspace: Path) -> dict[str, Any]:
    is_dir = path.is_dir()
    payload: dict[str, Any] = {
        "name": path.name or path.anchor,
        "path": _relative_path(path, workspace),
        "kind": "directory" if is_dir else "file",
        "has_children": is_dir,
    }
    if not is_dir:
        payload["language"] = _language_for_path(path)
        try:
            payload["size"] = path.stat().st_size
        except OSError:
            payload["unreadable"] = True
    try:
        stat = path.stat()
        payload["modified_at"] = datetime.fromtimestamp(
            stat.st_mtime,
            tz=timezone.utc,
        ).isoformat()
    except OSError:
        payload["unreadable"] = True
    return payload


def _resolve_tree_path(raw_path: str | None, *, scope: WorkspaceScope) -> Path:
    value = (raw_path or "").strip()
    if not value:
        value = str(scope.project_path)
    try:
        # The explorer is deliberately rooted at the selected project even in
        # full-access mode. Full access affects tools, not directory discovery.
        resolved = resolve_allowed_path(
            value,
            workspace=scope.project_path,
            allowed_root=scope.project_path,
            strict=True,
        )
    except FileNotFoundError as exc:
        raise WorkspaceTreeError(404, "workspace directory not found") from exc
    except WorkspaceBoundaryError as exc:
        raise WorkspaceTreeError(403, "path is outside the current workspace") from exc
    except OSError as exc:
        raise WorkspaceTreeError(400, "invalid workspace path") from exc
    return resolved


def _relative_path(path: Path, workspace: Path) -> str:
    try:
        relative = path.relative_to(workspace)
    except ValueError:
        return "."
    return relative.as_posix() or "."


def _bounded_int(value: int, *, minimum: int, maximum: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, number))


def _language_for_path(path: Path) -> str:
    name = path.name.lower()
    extension = path.suffix.lower().lstrip(".")
    if name == "dockerfile":
        return "dockerfile"
    return {
        "cjs": "javascript",
        "css": "css",
        "html": "html",
        "js": "javascript",
        "json": "json",
        "jsonl": "json",
        "jsx": "jsx",
        "md": "markdown",
        "mdx": "markdown",
        "py": "python",
        "pyi": "python",
        "scss": "scss",
        "sh": "bash",
        "toml": "toml",
        "ts": "typescript",
        "tsx": "tsx",
        "yaml": "yaml",
        "yml": "yaml",
    }.get(extension, extension or "text")
