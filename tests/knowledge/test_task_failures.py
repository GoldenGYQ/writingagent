from __future__ import annotations

import pytest

from nanobot.knowledge.service import KnowledgeService
from nanobot.knowledge.store import KnowledgeStore


def _project(tmp_path):
    source_root = tmp_path / "raw" / "sources"
    source_root.mkdir(parents=True)
    (source_root / "source.md").write_text("source\n", encoding="utf-8")
    service = KnowledgeService(KnowledgeStore(tmp_path))
    project_id = service.scan("raw/sources")["project"]["id"]
    return service, KnowledgeStore(tmp_path), project_id


def test_compile_failure_is_persisted_on_knowledge_task(tmp_path, monkeypatch):
    service, store, project_id = _project(tmp_path)

    def fail_compile(*_args, **_kwargs):
        raise RuntimeError("broken IR")

    monkeypatch.setattr("nanobot.knowledge.service.compile_project", fail_compile)
    with pytest.raises(RuntimeError, match="broken IR"):
        service.compile(project_id)

    task = store.get_task(project_id)
    project = store.get_project(project_id)
    assert task.phase == "compile_failed"
    assert task.status == "needs_changes"
    assert task.last_error == "broken IR"
    assert project.phase == "compile_failed"
    assert project.status == "needs_changes"


def test_validation_failure_is_persisted_on_knowledge_task(tmp_path, monkeypatch):
    service, store, project_id = _project(tmp_path)

    def fail_validate(*_args, **_kwargs):
        raise RuntimeError("invalid wiki")

    monkeypatch.setattr("nanobot.knowledge.service.validate_project", fail_validate)
    with pytest.raises(RuntimeError, match="invalid wiki"):
        service.validate(project_id)

    task = store.get_task(project_id)
    project = store.get_project(project_id)
    assert task.phase == "validation_failed"
    assert task.status == "needs_changes"
    assert task.last_error == "invalid wiki"
    assert project.phase == "validation_failed"
    assert project.status == "needs_changes"
