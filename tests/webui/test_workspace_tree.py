from pathlib import Path

import pytest

from nanobot.runtime_context import (
    WEBUI_FILE_CITATION_METADATA,
    WEBUI_FILE_CITATION_SOURCE,
    normalize_webui_file_citation,
    webui_file_citation_runtime_context,
)
from nanobot.security.workspace_access import build_workspace_scope
from nanobot.webui.workspace_tree import WorkspaceTreeError, workspace_tree_payload


def test_workspace_tree_is_bounded_and_hides_generated_directories(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "chapter.md").write_text("# Chapter\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.js").write_text("x", encoding="utf-8")

    payload = workspace_tree_payload(
        None,
        scope=build_workspace_scope(tmp_path, "restricted"),
        depth=3,
        limit=20,
    )

    names = {entry["name"] for entry in payload["entries"]}
    assert "docs" in names
    assert "node_modules" not in names
    docs = next(entry for entry in payload["entries"] if entry["name"] == "docs")
    assert docs["children"][0]["path"] == "docs/chapter.md"


def test_workspace_tree_rejects_file_as_root(tmp_path: Path) -> None:
    file_path = tmp_path / "note.md"
    file_path.write_text("note", encoding="utf-8")
    with pytest.raises(WorkspaceTreeError) as error:
        workspace_tree_payload(
            "note.md",
            scope=build_workspace_scope(tmp_path, "restricted"),
        )
    assert error.value.status == 404


def test_file_citation_runtime_context_keeps_path_and_lines() -> None:
    citation = {
        "path": "docs/chapter.md",
        "start_line": 12,
        "end_line": 14,
        "quote": "first\nsecond",
    }
    assert normalize_webui_file_citation(citation) == citation
    block = webui_file_citation_runtime_context({WEBUI_FILE_CITATION_METADATA: citation})
    assert block is not None
    assert block.source == WEBUI_FILE_CITATION_SOURCE
    assert "docs/chapter.md" in block.content
    assert "12" in block.content and "14" in block.content

