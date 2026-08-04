"""Structured review issues for document chapters."""

from __future__ import annotations

from nanobot.writing.models import ReviewIssue, new_id, utc_now
from nanobot.writing.store import WritingStore


class ReviewService:
    def __init__(self, workspace: str | WritingStore) -> None:
        self.store = workspace if isinstance(workspace, WritingStore) else WritingStore(workspace)

    def create_issue(
        self,
        project_id: str,
        *,
        document_id: str,
        chapter_id: str,
        description: str,
        kind: str = "general",
        severity: str = "medium",
        suggestion: str = "",
        revision_id: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        sources: list[str] | None = None,
    ) -> ReviewIssue:
        if not description.strip():
            raise ValueError("review description must not be empty")
        issue = ReviewIssue(
            id=new_id("review"),
            document_id=document_id,
            chapter_id=chapter_id,
            revision_id=revision_id,
            kind=kind,
            severity=severity,
            description=description.strip(),
            suggestion=suggestion.strip(),
            start_line=start_line,
            end_line=end_line,
            sources=list(sources or []),
        )
        self.store.save_review(project_id, issue)
        return issue

    def update_status(self, project_id: str, review_id: str, status: str) -> ReviewIssue:
        issue = self.store.get_review(project_id, review_id)
        if status not in {"open", "accepted", "dismissed", "fixed"}:
            raise ValueError("invalid review status")
        issue.status = status
        issue.updated_at = utc_now()
        self.store.save_review(project_id, issue)
        return issue

    def record_changeset_decision(
        self,
        project_id: str,
        *,
        document_id: str,
        chapter_id: str,
        changeset_id: str,
        decision: str,
        feedback: str = "",
        revision_id: str | None = None,
        reviewer: str = "user",
    ) -> ReviewIssue:
        """Persist the human decision that closed a ChangeSet review.

        A ChangeSet is the proposal and this record is the review event.  Keeping
        both lets the UI show the latest status while retaining an append-only
        audit trail of rejected drafts and their feedback.
        """
        if decision not in {"accepted", "rejected"}:
            raise ValueError("invalid changeset decision")
        clean_feedback = feedback.strip()
        description = clean_feedback or (
            "ChangeSet approved by the user."
            if decision == "accepted"
            else "ChangeSet rejected without additional feedback."
        )
        issue = ReviewIssue(
            id=new_id("review"),
            document_id=document_id,
            chapter_id=chapter_id,
            revision_id=revision_id,
            kind="changeset_feedback",
            severity="medium",
            description=description,
            suggestion=clean_feedback if decision == "rejected" else "",
            changeset_id=changeset_id,
            decision=decision,
            status="accepted" if decision == "accepted" else "open",
            sources=[reviewer] if reviewer else [],
        )
        self.store.save_review(project_id, issue)
        return issue

    def list(self, project_id: str, *, document_id: str | None = None, chapter_id: str | None = None) -> list[ReviewIssue]:
        return self.store.list_reviews(project_id, document_id=document_id, chapter_id=chapter_id)
