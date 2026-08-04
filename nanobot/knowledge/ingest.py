"""Observable source-ingestion adapter contracts.

The Knowledge Runtime keeps ingestion separate from extraction and publishing.
This module only describes how an Agent should read a raw source; the raw bytes
are still mirrored by :class:`KnowledgeService` and the model produces the
typed IR through ``knowledge_extract``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IngestionAdapterSpec:
    """A bounded reader contract exposed in the source manifest."""

    name: str
    kind: str
    extensions: tuple[str, ...]
    extraction_mode: str
    instruction: str
    requires_vision: bool = False

    def metadata(self) -> dict[str, str | bool]:
        return {
            "ingestion_adapter": self.name,
            "extraction_mode": self.extraction_mode,
            "instruction": self.instruction,
            "requires_vision": self.requires_vision,
        }


_TEXT = IngestionAdapterSpec(
    name="text_lines",
    kind="text",
    extensions=(
        ".md", ".markdown", ".txt", ".rst", ".py", ".js", ".ts", ".tsx", ".jsx",
        ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".tex", ".csv",
    ),
    extraction_mode="line_bounded",
    instruction="Use read_file with offset/limit and preserve source line anchors.",
)
_MARKDOWN = IngestionAdapterSpec(
    name="markdown_lines",
    kind="markdown",
    extensions=(".md", ".markdown"),
    extraction_mode="line_bounded",
    instruction="Use read_file with offset/limit and preserve Markdown headings and source line anchors.",
)
_PDF = IngestionAdapterSpec(
    name="pdf_pages",
    kind="pdf",
    extensions=(".pdf",),
    extraction_mode="page_bounded",
    instruction="Use read_file with a bounded pages range; do not inject the whole PDF into context.",
)
_HTML = IngestionAdapterSpec(
    name="html_text",
    kind="html",
    extensions=(".html", ".htm"),
    extraction_mode="text_bounded",
    instruction="Read the saved HTML as source text and preserve its local path as evidence.",
)
_IMAGE = IngestionAdapterSpec(
    name="vision_ocr",
    kind="image",
    extensions=(".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"),
    extraction_mode="vision_or_ocr",
    instruction="Use vision/OCR to extract only the needed regions and record the image path as evidence.",
    requires_vision=True,
)

_BY_EXTENSION = {
    extension: spec
    for spec in (_TEXT, _MARKDOWN, _PDF, _HTML, _IMAGE)
    for extension in spec.extensions
}


def adapter_for_path(path: str | Path) -> IngestionAdapterSpec | None:
    """Return the reader contract for a path suffix, if supported."""
    return _BY_EXTENSION.get(Path(path).suffix.lower())


def supported_source_suffixes() -> frozenset[str]:
    """Return the suffixes that ``knowledge_scan`` may mirror into ``raw``."""
    return frozenset(_BY_EXTENSION)
