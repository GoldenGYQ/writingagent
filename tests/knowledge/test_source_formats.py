from __future__ import annotations

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
    mirrored = tmp_path / "wikis" / project["id"] / "raw" / "sources" / "paper.pdf"
    assert mirrored.read_bytes() == pdf_bytes
