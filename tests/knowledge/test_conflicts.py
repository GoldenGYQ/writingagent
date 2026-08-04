from __future__ import annotations

from nanobot.knowledge.service import KnowledgeService
from nanobot.knowledge.store import KnowledgeStore


def test_validate_reports_cross_source_page_conflict(tmp_path):
    source_root = tmp_path / "raw" / "sources"
    source_root.mkdir(parents=True)
    (source_root / "a.md").write_text("a\n", encoding="utf-8")
    (source_root / "b.md").write_text("b\n", encoding="utf-8")
    service = KnowledgeService(KnowledgeStore(tmp_path))
    project_id = service.scan("raw/sources", title="Conflict test")["project"]["id"]
    service.extract(
        project_id,
        "a.md",
        pages=[{
            "type": "concept",
            "title": "Shared concept",
            "slug": "shared-concept",
            "body": "The first source says alpha.",
        }],
    )
    service.extract(
        project_id,
        "b.md",
        pages=[{
            "type": "concept",
            "title": "Shared concept",
            "slug": "shared-concept",
            "body": "The second source says beta.",
        }],
    )
    service.compile(project_id)

    validation = service.validate(project_id)

    assert validation["passed"] is False
    conflicts = [issue for issue in validation["issues"] if issue["kind"] == "conflict"]
    assert conflicts
    assert conflicts[0]["sources"] == ["a.md", "b.md"]
    published = service.publish(project_id)
    assert published["published"] is False
