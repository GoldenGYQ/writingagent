"""Document and Chapter services.

The service deliberately treats Chapter as a semantic object with its own
revision pointer, while keeping Markdown as the editable representation.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from nanobot.writing.artifact import ArtifactService
from nanobot.writing.models import Chapter, Document, WritingProject, new_id, utc_now
from nanobot.writing.store import WritingNotFoundError, WritingStore


class DocumentService:
    def __init__(self, workspace: str | Path | WritingStore) -> None:
        self.store = workspace if isinstance(workspace, WritingStore) else WritingStore(workspace)
        self.artifacts = ArtifactService(self.store)

    def create_project(
        self,
        title: str,
        *,
        goal: str = "",
        style: str = "",
        outline: list[dict[str, Any]] | None = None,
    ) -> WritingProject:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("project title must not be empty")
        project = WritingProject(
            id=new_id("project"),
            title=clean_title,
            goal=goal.strip(),
            style=style.strip(),
            outline=list(outline or []),
        )
        return self.store.create_project(project)

    def get_project(self, project_id: str) -> WritingProject:
        return self.store.get_project(project_id)

    def create_document(
        self,
        project_id: str,
        title: str,
        *,
        document_id: str | None = None,
    ) -> Document:
        project = self.store.get_project(project_id)
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("document title must not be empty")
        document = Document(
            id=document_id or new_id("document"),
            title=clean_title,
            project_id=project.id,
            artifact_id=new_id("artifact"),
            path=f"documents/{document_id or 'pending'}",
        )
        document.path = f"documents/{document.id}"
        self.store.save_document(document)
        self.artifacts.update_from_document(document)
        if document.id not in project.document_ids:
            project.document_ids.append(document.id)
            project.updated_at = utc_now()
            self.store.save_project(project)
        return document

    def get_document(self, project_id: str, document_id: str) -> Document:
        return self.store.get_document(project_id, document_id)

    def update_document(
        self,
        project_id: str,
        document_id: str,
        *,
        title: str | None = None,
        status: str | None = None,
    ) -> Document:
        document = self.get_document(project_id, document_id)
        if title is not None:
            clean_title = title.strip()
            if not clean_title:
                raise ValueError("document title must not be empty")
            document.title = clean_title
        if status is not None:
            document.status = status.strip() or document.status
        document.updated_at = utc_now()
        self.store.save_document(document)
        self.artifacts.update_from_document(document)
        return document

    def create_chapter(
        self,
        document: Document,
        title: str,
        *,
        content: str = "",
        chapter_id: str | None = None,
        order: int | None = None,
        summary: str = "",
    ) -> Chapter:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("chapter title must not be empty")
        next_order = max((chapter.order for chapter in document.chapters), default=0) + 1
        chapter = Chapter(
            id=chapter_id or new_id("chapter"),
            title=clean_title,
            order=order if order is not None else next_order,
            path=f"{document.path}/chapters/{chapter_id or 'pending'}.md",
            summary=summary.strip(),
            word_count=len(content.split()),
        )
        chapter.path = f"{document.path}/chapters/{chapter.id}.md"
        document.chapters.append(chapter)
        document.chapters.sort(key=lambda item: item.order)
        document.updated_at = utc_now()
        self.store.write_chapter(document, chapter.id, content)
        self.store.save_document(document)
        self.artifacts.update_from_document(document)
        return chapter

    def get_chapter(self, document: Document, chapter_id: str) -> Chapter:
        for chapter in document.chapters:
            if chapter.id == chapter_id:
                return chapter
        raise WritingNotFoundError(f"chapter not found: {chapter_id}")

    def read_chapter(self, document: Document, chapter_id: str) -> tuple[Chapter, str]:
        chapter = self.get_chapter(document, chapter_id)
        return chapter, self.store.read_chapter(document, chapter.id)

    def update_outline(
        self,
        project_id: str,
        outline: list[dict[str, Any]],
    ) -> WritingProject:
        project = self.store.get_project(project_id)
        project.outline = list(outline)
        project.updated_at = utc_now()
        self.store.save_project(project)
        return project

    @staticmethod
    def update_chapter_metadata(
        document: Document,
        chapter_id: str,
        *,
        status: str | None = None,
        summary: str | None = None,
    ) -> Chapter:
        for index, chapter in enumerate(document.chapters):
            if chapter.id != chapter_id:
                continue
            updated = replace(
                chapter,
                status=status if status is not None else chapter.status,
                summary=summary if summary is not None else chapter.summary,
                word_count=chapter.word_count,
            )
            document.chapters[index] = updated
            document.updated_at = utc_now()
            return updated
        raise WritingNotFoundError(f"chapter not found: {chapter_id}")
