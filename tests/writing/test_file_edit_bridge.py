from __future__ import annotations

import pytest

from nanobot.writing.document import DocumentService
from nanobot.writing.file_edit import WritingFileEditError, propose_file_changeset
from nanobot.writing.store import WritingStore


def _chapter(tmp_path):
    documents = DocumentService(tmp_path)
    project = documents.create_project("WebUI editing")
    document = documents.create_document(project.id, "Draft")
    chapter = documents.create_chapter(document, "Introduction", content="old\n")
    return project, document, chapter


def test_source_edit_becomes_review_changeset_without_mutating_chapter(tmp_path):
    project, document, chapter = _chapter(tmp_path)
    store = WritingStore(tmp_path)
    result = propose_file_changeset(
        store,
        raw_path=str(store.chapter_path(project.id, document.id, chapter.id)),
        content="new\n",
        context={"project_id": project.id, "document_id": document.id, "chapter_id": chapter.id},
        execution_policy="ask",
    )

    assert result["status"] == "review"
    assert result["changeset"]["status"] == "review"
    assert store.read_chapter(document, chapter.id) == "old\n"


def test_source_edit_auto_policy_applies_revision(tmp_path):
    project, document, chapter = _chapter(tmp_path)
    store = WritingStore(tmp_path)
    result = propose_file_changeset(
        store,
        raw_path=chapter.path,
        content="new\n",
        context={"project_id": project.id, "document_id": document.id, "chapter_id": chapter.id},
        execution_policy="auto",
    )

    assert result["status"] == "applied"
    assert result["revision"]
    assert store.read_chapter(document, chapter.id) == "new\n"


def test_source_edit_rejects_unmanaged_path_and_read_only_policy(tmp_path):
    project, document, chapter = _chapter(tmp_path)
    store = WritingStore(tmp_path)
    context = {"project_id": project.id, "document_id": document.id, "chapter_id": chapter.id}
    with pytest.raises(WritingFileEditError, match="not the current managed"):
        propose_file_changeset(
            store,
            raw_path="notes/unmanaged.md",
            content="new\n",
            context=context,
            execution_policy="ask",
        )
    with pytest.raises(WritingFileEditError, match="read-only"):
        propose_file_changeset(
            store,
            raw_path=chapter.path,
            content="new\n",
            context=context,
            execution_policy="read_only",
        )

