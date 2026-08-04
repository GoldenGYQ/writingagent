"""ChangeSet generation and approval for document assets."""

from __future__ import annotations

import difflib
from typing import Any, Iterable, Mapping

from nanobot.writing.models import (
    Change,
    ChangeSet,
    Chapter,
    Document,
    content_hash,
    new_id,
    utc_now,
)
from nanobot.writing.review import ReviewService
from nanobot.writing.revision import RevisionConflict, RevisionService
from nanobot.writing.store import WritingStore


def _diff_stats(unified_diff: str) -> tuple[int, int]:
    added = 0
    deleted = 0
    for line in unified_diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            deleted += 1
    return added, deleted


class ChangeSetService:
    def __init__(self, workspace: str | WritingStore) -> None:
        self.store = workspace if isinstance(workspace, WritingStore) else WritingStore(workspace)
        self.revisions = RevisionService(self.store)
        self.reviews = ReviewService(self.store)

    def propose(
        self,
        document: Document,
        chapter: Chapter,
        proposed_content: str,
        *,
        reason: str,
        impact: str = "",
        sources: Iterable[Mapping[str, Any]] = (),
        base_revision_id: str | None = None,
    ) -> ChangeSet:
        before = self.store.read_chapter(document, chapter.id)
        expected_base = chapter.current_revision_id if base_revision_id is None else base_revision_id
        if expected_base != chapter.current_revision_id:
            raise RevisionConflict(
                f"chapter revision conflict; current revision is {chapter.current_revision_id or 'initial'}"
            )
        after = proposed_content.replace("\r\n", "\n")
        if before == after:
            raise ValueError("proposed chapter content is unchanged")
        unified = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=chapter.path,
                tofile=chapter.path,
                n=3,
            )
        )
        added, deleted = _diff_stats(unified)
        change = Change(
            path=chapter.path,
            before_hash=content_hash(before),
            after_hash=content_hash(after),
            unified_diff=unified[:50_000],
            added=added,
            deleted=deleted,
        )
        changeset = ChangeSet(
            id=new_id("changeset"),
            artifact_id=document.artifact_id,
            document_id=document.id,
            chapter_id=chapter.id,
            base_revision_id=expected_base,
            proposed_content=after,
            changes=[change],
            reason=reason.strip(),
            impact=impact.strip(),
            sources=[dict(source) for source in sources],
            status="review",
        )
        self.store.save_changeset(document.project_id, changeset)
        return changeset

    def get(self, project_id: str, changeset_id: str) -> ChangeSet:
        return self.store.get_changeset(project_id, changeset_id)

    def approve(self, document: Document, changeset: ChangeSet, *, author: str = "agent") -> ChangeSet:
        if changeset.status == "applied":
            return changeset
        if changeset.status not in {"review", "approved"}:
            raise ValueError(f"changeset cannot be approved from status {changeset.status}")
        changeset.status = "approved"
        changeset.approved_at = utc_now()
        changeset.updated_at = utc_now()
        self.store.save_changeset(document.project_id, changeset)
        chapter = next((item for item in document.chapters if item.id == changeset.chapter_id), None)
        if chapter is None:
            raise ValueError(f"chapter not found: {changeset.chapter_id}")
        revision = self.revisions.apply_changeset(document, chapter, changeset, author=author)
        changeset.status = "applied"
        changeset.applied_revision_id = revision.id
        changeset.reviewed_at = utc_now()
        changeset.reviewed_by = author
        changeset.updated_at = utc_now()
        self.store.save_changeset(document.project_id, changeset)
        self.reviews.record_changeset_decision(
            document.project_id,
            document_id=document.id,
            chapter_id=changeset.chapter_id,
            changeset_id=changeset.id,
            decision="accepted",
            revision_id=revision.id,
            reviewer=author,
        )
        return changeset

    def reject(
        self,
        project_id: str,
        changeset_id: str,
        *,
        feedback: str = "",
        reviewer: str = "user",
    ) -> ChangeSet:
        changeset = self.store.get_changeset(project_id, changeset_id)
        if changeset.status == "applied":
            raise ValueError("an applied changeset cannot be rejected")
        changeset.status = "rejected"
        changeset.feedback = feedback.strip()
        changeset.reviewed_at = utc_now()
        changeset.reviewed_by = reviewer
        changeset.updated_at = utc_now()
        self.store.save_changeset(project_id, changeset)
        self.reviews.record_changeset_decision(
            project_id,
            document_id=changeset.document_id,
            chapter_id=changeset.chapter_id,
            changeset_id=changeset.id,
            decision="rejected",
            feedback=changeset.feedback,
            revision_id=changeset.base_revision_id,
            reviewer=reviewer,
        )
        return changeset
