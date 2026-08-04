from __future__ import annotations

from nanobot.knowledge.ingest import adapter_for_path
from nanobot.knowledge.service import KnowledgeService
from nanobot.knowledge.store import KnowledgeStore


def test_scan_mirrors_pdf_as_a_raw_source(tmp_path) -> None:
    source_root = tmp_path / "raw" / "sources"
    source_root.mkdir(parents=True)
    pdf_bytes = b"%PDF-1.7\nminimal test payload\n"
    (source_root / "paper.pdf").write_bytes(pdf_bytes)

    result = KnowledgeService(KnowledgeStore(tmp_path)).scan("raw/sources", title="PDF Wiki")
    project = result["project"]
    source = project["sources"][0]

    assert result["documents"] == ["paper.pdf"]
    assert source["kind"] == "pdf"
    assert source["metadata"]["ingestion_adapter"] == "pdf_pages"
    mirrored = tmp_path / "wikis" / project["id"] / "raw" / "sources" / "paper.pdf"
    assert mirrored.read_bytes() == pdf_bytes


def test_ingestion_adapter_contract_covers_html_and_vision_sources() -> None:
    html = adapter_for_path("article.html")
    image = adapter_for_path("figure.png")

    assert html is not None
    assert html.name == "html_text"
    assert html.extraction_mode == "text_bounded"
    assert image is not None
    assert image.name == "vision_ocr"
    assert image.requires_vision is True


def test_scan_records_adapter_metadata_and_mirrors_non_text_sources(tmp_path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    (source_root / "article.html").write_text("<article>evidence</article>", encoding="utf-8")
    image_bytes = b"not-a-real-image-but-a-binary-source"
    (source_root / "figure.png").write_bytes(image_bytes)

    result = KnowledgeService(KnowledgeStore(tmp_path)).scan("sources", title="Mixed Wiki")
    project = result["project"]
    sources = {item["relative_path"]: item for item in project["sources"]}

    assert set(sources) == {"article.html", "figure.png"}
    assert sources["article.html"]["metadata"]["ingestion_adapter"] == "html_text"
    assert sources["figure.png"]["metadata"]["ingestion_adapter"] == "vision_ocr"
    assert sources["figure.png"]["metadata"]["requires_vision"] is True
    project_root = tmp_path / "wikis" / project["id"] / "raw" / "sources"
    assert (project_root / "article.html").read_text(encoding="utf-8") == "<article>evidence</article>"
    assert (project_root / "figure.png").read_bytes() == image_bytes
