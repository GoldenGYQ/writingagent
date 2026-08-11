"""Bridge WebUI source edits to the managed Writing ChangeSet workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from nanobot.writing.changeset import ChangeSetService
from nanobot.writing.context import set_writing_context
from nanobot.writing.store import WritingStore

MAX_WEBUI_EDIT_CHARS = 200_000


class WritingFileEditError(ValueError):
    """Raised when a WebUI source edit cannot enter the Writing workflow."""


def propose_file_changeset(
    store: WritingStore,
    *,
    raw_path: str,
    content: str,
    context: Mapping[str, str],
    execution_policy: str,
    reason: str = "Edit from WebUI source editor",
    author: str = "user",
) -> dict[str, object]:
    """Create a managed ChangeSet and optionally apply it under ``auto`` policy."""

    if execution_policy == "read_only":
        raise WritingFileEditError("read-only execution policy does not allow source edits")
    if execution_policy not in {"ask", "auto"}:
        raise WritingFileEditError(f"unsupported execution policy: {execution_policy}")
    if len(content) > MAX_WEBUI_EDIT_CHARS:
        raise WritingFileEditError("edited content is too large")

    project_id = context.get("project_id", "").strip()
    document_id = context.get("document_id", "").strip()
    chapter_id = context.get("chapter_id", "").strip()
    if not project_id or not document_id or not chapter_id:
        raise WritingFileEditError(
            "select a managed Writing document and chapter before saving this file"
        )

    document = store.get_document(project_id, document_id)
    chapter = next((item for item in document.chapters if item.id == chapter_id), None)
    if chapter is None:
        raise WritingFileEditError(f"chapter not found: {chapter_id}")

    chapter_path = store.chapter_path(project_id, document.id, chapter.id).resolve()
    requested_path = Path(raw_path).expanduser().resolve(strict=False)
    semantic_path = Path(raw_path).as_posix().replace("\\", "/").lstrip("./")
    expected_semantic_paths = {
        chapter.path.replace("\\", "/").lstrip("./"),
        f"writing/{project_id}/{chapter.path}".replace("\\", "/").lstrip("./"),
    }
    if requested_path != chapter_path and semantic_path not in expected_semantic_paths:
        raise WritingFileEditError("the selected file is not the current managed Writing chapter")

    changeset = ChangeSetService(store).propose(
        document,
        chapter,
        content,
        reason=reason,
    )
    result: dict[str, object] = {
        "changeset": changeset.to_dict(),
        "status": "review",
        "revision": None,
        "context": dict(context),
    }
    if execution_policy == "auto":
        applied = ChangeSetService(store).approve(document, changeset, author=author)
        revision = (
            store.get_revision(project_id, applied.applied_revision_id)
            if applied.applied_revision_id
            else None
        )
        result.update(
            {
                "changeset": applied.to_dict(),
                "status": "applied",
                "revision": revision.to_dict() if revision else None,
                "context": set_writing_context(
                    dict(context),
                    project_id=project_id,
                    document_id=document.id,
                    chapter_id=chapter.id,
                    revision_id=revision.id if revision else None,
                ),
            }
        )
    return result
