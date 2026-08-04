"""Artifact-level operations for the Writing Document Runtime."""

from __future__ import annotations

from nanobot.writing.models import Artifact, Document, utc_now
from nanobot.writing.store import WritingNotFoundError, WritingStore


class ArtifactService:
    """Maintain the metadata identity that sits above file representations."""

    def __init__(self, store: WritingStore) -> None:
        self.store = store

    def for_document(self, document: Document) -> Artifact:
        try:
            return self.store.get_artifact(document.project_id, document.id)
        except WritingNotFoundError:
            artifact = Artifact(
                id=document.artifact_id,
                title=document.title,
                artifact_type="document",
                project_id=document.project_id,
                document_id=document.id,
                status=document.status,
                current_revision_id=document.current_revision_id,
                created_at=document.created_at,
                updated_at=document.updated_at,
            )
            self.store.save_artifact(artifact, document_id=document.id)
            return artifact

    def update_from_document(self, document: Document) -> Artifact:
        artifact = self.for_document(document)
        artifact.title = document.title
        artifact.status = document.status
        artifact.current_revision_id = document.current_revision_id
        artifact.document_id = document.id
        artifact.updated_at = utc_now()
        self.store.save_artifact(artifact, document_id=document.id)
        return artifact
