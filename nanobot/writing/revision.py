"""Immutable Revision creation, comparison and rollback."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any

from nanobot.writing.artifact import ArtifactService
from nanobot.writing.models import Chapter, Document, Revision, content_hash, new_id, utc_now
from nanobot.writing.store import WritingStore


class RevisionConflictError(RuntimeError):
    """The proposed change was based on an older chapter version."""


RevisionConflict = RevisionConflictError


@dataclass(frozen=True)
class RevisionComparison:
    from_revision: str | None
    to_revision: str | None
    unified_diff: str
    added: int
    deleted: int


class RevisionService:
    def __init__(self, store: WritingStore) -> None:
        self.store = store

    def _next_number(self, document: Document, chapter: Chapter) -> int:
        if not chapter.current_revision_id:
            return 1
        current = self.store.get_revision(document.project_id, chapter.current_revision_id)
        return current.number + 1

    def apply_changeset(
        self,
        document: Document,
        chapter: Chapter,
        changeset: Any,
        *,
        author: str = "agent",
    ) -> Revision:
        current_content = self.store.read_chapter(document, chapter.id)
        current_hash = content_hash(current_content)
        expected_hash = changeset.changes[0].before_hash if changeset.changes else current_hash
        if changeset.base_revision_id != chapter.current_revision_id or current_hash != expected_hash:
            raise RevisionConflict(
                f"chapter revision conflict; current revision is {chapter.current_revision_id or 'initial'}"
            )
        revision = Revision(
            id=new_id("rev"),
            artifact_id=document.artifact_id,
            project_id=document.project_id,
            document_id=document.id,
            chapter_id=chapter.id,
            number=self._next_number(document, chapter),
            content=changeset.proposed_content,
            content_hash=content_hash(changeset.proposed_content),
            base_revision_id=chapter.current_revision_id,
            author=author,
            reason=changeset.reason,
            metadata={"project_id": document.project_id},
        )
        self.store.save_revision(revision)
        self.store.write_chapter(document, chapter.id, revision.content)
        chapter.current_revision_id = revision.id
        chapter.word_count = len(revision.content.split())
        chapter.status = "complete" if revision.content.strip() else chapter.status
        document.current_revision_id = revision.id
        document.updated_at = utc_now()
        self.store.save_document(document)
        ArtifactService(self.store).update_from_document(document)
        return revision

    def list(self, project_id: str, *, document_id: str | None = None, chapter_id: str | None = None) -> list[Revision]:
        return self.store.list_revisions(project_id, document_id=document_id, chapter_id=chapter_id)

    def compare(
        self,
        project_id: str,
        from_revision_id: str,
        to_revision_id: str,
    ) -> RevisionComparison:
        """Return a bounded line diff between two immutable snapshots."""

        before = self.store.get_revision(project_id, from_revision_id)
        after = self.store.get_revision(project_id, to_revision_id)
        if (before.document_id, before.chapter_id) != (after.document_id, after.chapter_id):
            raise ValueError("revisions must belong to the same chapter")
        unified = "".join(
            difflib.unified_diff(
                before.content.splitlines(keepends=True),
                after.content.splitlines(keepends=True),
                fromfile=before.id,
                tofile=after.id,
                n=3,
            )
        )[:50_000]
        added = sum(1 for line in unified.splitlines() if line.startswith("+") and not line.startswith("+++"))
        deleted = sum(1 for line in unified.splitlines() if line.startswith("-") and not line.startswith("---"))
        return RevisionComparison(
            from_revision=before.id,
            to_revision=after.id,
            unified_diff=unified,
            added=added,
            deleted=deleted,
        )

    def restore(self, document: Document, chapter: Chapter, revision_id: str, *, author: str = "user") -> Revision:
        target = self.store.get_revision(document.project_id, revision_id)
        if target.document_id != document.id or target.chapter_id != chapter.id:
            raise ValueError("revision does not belong to the requested chapter")
        current_content = self.store.read_chapter(document, chapter.id)
        if content_hash(current_content) == target.content_hash:
            return target
        from nanobot.writing.changeset import ChangeSetService

        changeset = ChangeSetService(self.store).propose(
            document,
            chapter,
            target.content,
            reason=f"Restore revision {target.id}",
            base_revision_id=chapter.current_revision_id,
        )
        return self.apply_changeset(document, chapter, changeset, author=author)
