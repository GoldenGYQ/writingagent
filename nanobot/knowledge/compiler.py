"""Compile Knowledge IR into source-linked wiki pages and a graph snapshot."""

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from nanobot.knowledge.models import (
    PAGE_TYPES,
    KnowledgeIR,
    KnowledgePage,
    KnowledgeProject,
    KnowledgeRelation,
)
from nanobot.knowledge.store import KnowledgeStore

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_INVALID_SLUG_CHARS = re.compile(r"[\\/:*?\"<>|\x00-\x1f]+")
_MULTISPACE_RE = re.compile(r"\s+")


def safe_slug(value: str) -> str:
    """Create a stable, readable, path-safe Unicode slug."""
    cleaned = _INVALID_SLUG_CHARS.sub("-", value.strip())
    cleaned = _MULTISPACE_RE.sub("-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned).strip(".-")
    return (cleaned or "untitled")[:120]


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = value.strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _canonical_slug(value: str, known_slugs: set[str]) -> str:
    slug = safe_slug(value)
    return next((known for known in known_slugs if known.casefold() == slug.casefold()), slug)


def _frontmatter_list(value: Any) -> str:
    return json.dumps(_unique([str(item) for item in value if str(item).strip()]), ensure_ascii=False)


def render_page(page: KnowledgePage) -> str:
    """Render one page using the reference wiki frontmatter contract."""
    frontmatter_lines = [
        "---",
        f"type: {page.type}",
        f"title: {json.dumps(page.title, ensure_ascii=False)}",
        f"tags: {_frontmatter_list(page.tags)}",
        f"related: {_frontmatter_list(page.related)}",
        f"sources: {_frontmatter_list(page.sources)}",
        f"created: {page.created}",
        f"updated: {page.updated}",
    ]
    if page.type == "source":
        for key in ("authors", "year", "url", "venue"):
            value = page.metadata.get(key, [] if key == "authors" else "")
            serialized = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
            frontmatter_lines.append(f"{key}: {serialized}")
    frontmatter = "\n".join([*frontmatter_lines, "---"])
    body = page.body.strip()
    if not body:
        body = f"# {page.title}\n"
    elif not body.lstrip().startswith("#"):
        body = f"# {page.title}\n\n{body}"
    return f"{frontmatter}\n\n{body.rstrip()}\n"


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse the reference wiki YAML frontmatter safely.

    Nanobot emits JSON-compatible lists, while existing BoClaw-style wikis
    commonly use unquoted YAML scalars such as ``tags: [Agent, 运行时]``.
    ``BaseLoader``-style coercion through ``safe_load`` preserves both forms
    without constructing arbitrary Python objects; the small line parser below
    remains a fallback for malformed legacy pages.
    """
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) != 3:
        return {}, content
    try:
        import yaml

        loaded = yaml.safe_load(parts[1])
        if isinstance(loaded, dict):
            loaded_mapping = cast(dict[Any, Any], loaded)
            parsed_metadata: dict[str, Any] = {}
            for key, value in loaded_mapping.items():
                normalized_key = str(key)
                if isinstance(value, list):
                    parsed_metadata[normalized_key] = [
                        str(item) for item in value if item is not None
                    ]
                elif value is None:
                    parsed_metadata[normalized_key] = ""
                else:
                    parsed_metadata[normalized_key] = str(value)
            return parsed_metadata, parts[2].lstrip("\r\n")
    except Exception:
        # Keep the legacy parser as a best-effort fallback for incomplete
        # frontmatter; validation will still report malformed fields.
        pass
    metadata: dict[str, Any] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key = key.strip()
        raw = raw.strip()
        if raw.startswith("["):
            try:
                parsed = ast.literal_eval(raw)
            except (SyntaxError, ValueError):
                parsed = []
            metadata[key] = parsed if isinstance(parsed, list) else []
        elif raw.startswith('"'):
            try:
                metadata[key] = json.loads(raw)
            except json.JSONDecodeError:
                metadata[key] = raw.strip('"')
        else:
            metadata[key] = raw
    return metadata, parts[2].lstrip("\r\n")


def _merge_existing(store: KnowledgeStore, project_id: str, page: KnowledgePage) -> KnowledgePage:
    """Merge a new extraction into an existing page instead of overwriting it."""
    path = store.page_path(project_id, page.type, page.slug)
    if not path.exists():
        return page
    try:
        metadata, old_body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return page
    old_tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else []
    old_related = metadata.get("related") if isinstance(metadata.get("related"), list) else []
    old_sources = metadata.get("sources") if isinstance(metadata.get("sources"), list) else []
    body = old_body.strip()
    addition = page.body.strip()
    if addition and addition not in body:
        body = f"{body}\n\n## Knowledge update ({page.updated})\n\n{addition}".strip()
    return KnowledgePage(
        type=page.type,
        title=page.title or str(metadata.get("title") or page.slug),
        slug=page.slug,
        body=body,
        tags=_unique([*map(str, old_tags), *page.tags]),
        related=_unique([*map(str, old_related), *page.related]),
        sources=_unique([*map(str, old_sources), *page.sources]),
        source_path=page.source_path,
        created=str(metadata.get("created") or page.created),
        updated=page.updated,
        metadata=page.metadata,
    )


def _page_from_entity(entity: dict[str, Any], source_path: str) -> KnowledgePage | None:
    name = str(entity.get("name") or "").strip()
    if not name:
        return None
    return KnowledgePage(
        type=str(entity.get("type") or "entity"),
        title=name,
        slug=safe_slug(name),
        body=str(entity.get("description") or ""),
        source_path=source_path,
        sources=[source_path] if source_path else [],
    )


def _fallback_source_page(source_path: str, content: str) -> KnowledgePage:
    """Create a conservative source page when extraction omitted page drafts."""
    title = Path(source_path).stem or source_path
    paragraphs = [part.strip() for part in content.split("\n\n") if part.strip()]
    excerpt = "\n\n".join(paragraphs[:3])[:1800]
    return KnowledgePage(
        type="source",
        title=title,
        slug=safe_slug(title),
        body=(f"# {title}\n\n{excerpt}" if excerpt else f"# {title}"),
        source_path=source_path,
        sources=[source_path],
    )


def _read_source_text(store: KnowledgeStore, project: KnowledgeProject, source_path: str) -> str:
    source = next(
        (item for item in project.sources if item.relative_path == source_path),
        None,
    )
    if source is not None and source.raw_relative_path:
        path = store.raw_path(project.id, source.raw_relative_path)
    else:
        path = Path(source_path)
        if not path.is_absolute():
            path = Path(project.source_root) / path
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _extraction_conflicts(store: KnowledgeStore, project_id: str) -> list[dict[str, Any]]:
    """Find contradictory page drafts from different source documents."""
    observations: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for ir in store.list_ir(project_id):
        pages = list(ir.pages)
        pages.extend(
            KnowledgePage(
                type=str(entity.type or "entity"),
                title=entity.name,
                slug=safe_slug(entity.name),
                body=entity.description,
                source_path=entity.source_path or ir.source_path,
            )
            for entity in ir.entities
            if entity.name.strip()
        )
        for page in pages:
            source_path = page.source_path or ir.source_path
            body = page.body.strip()
            if not source_path or not body:
                continue
            key = (page.type, safe_slug(page.slug or page.title))
            observations[key].append((source_path, body))

    issues: list[dict[str, Any]] = []
    for (page_type, slug), values in observations.items():
        sources = sorted({source for source, _ in values})
        bodies = {body for _, body in values}
        if len(sources) < 2 or len(bodies) < 2:
            continue
        issues.append({
            "kind": "conflict",
            "path": f"knowledge/ir/{page_type}/{slug}",
            "message": f"conflicting extracted content for {page_type}/{slug} from {len(sources)} sources",
            "sources": sources[:20],
        })
    return issues


def compile_project(store: KnowledgeStore, project_id: str) -> dict[str, Any]:
    project = store.get_project(project_id)
    ir_values = store.list_ir(project_id)
    page_map: dict[tuple[str, str], KnowledgePage] = {}
    relations: list[KnowledgeRelation] = []

    for ir in ir_values:
        pages = list(ir.pages)
        pages.extend(
            page
            for entity in (entity.to_dict() for entity in ir.entities)
            if (page := _page_from_entity(entity, ir.source_path)) is not None
        )
        if not pages:
            pages = [_fallback_source_page(ir.source_path, _read_source_text(store, project, ir.source_path))]
        for page in pages:
            page.type = page.type if page.type in PAGE_TYPES else "concept"
            page.slug = safe_slug(page.slug or page.title)
            page.source_path = page.source_path or ir.source_path
            if page.source_path and page.source_path not in page.sources:
                page.sources.append(page.source_path)
            page.updated = _today()
            key = (page.type, page.slug)
            page_map[key] = _merge_existing(store, project_id, page)
        relations.extend(ir.relations)

    compiled: list[KnowledgePage] = list(page_map.values())
    for page in compiled:
        store.write_page(project_id, page, render_page(page))

    # The index and overview are deterministic projections, not extraction output.
    grouped: dict[str, list[KnowledgePage]] = defaultdict(list)
    for page in sorted(compiled, key=lambda item: (item.type, item.title.casefold())):
        grouped[page.type].append(page)
    index_lines = ["# Wiki Index", ""]
    for page_type in PAGE_TYPES:
        if page_type == "overview" or not grouped.get(page_type):
            continue
        index_lines.extend([f"## {page_type.title()}s", ""])
        for page in grouped[page_type]:
            description = next(
                (line.strip("# ").strip() for line in page.body.splitlines() if line.strip() and not line.startswith("#")),
                page.title,
            )
            index_lines.append(f"- [[{page.slug}]] — {description[:240]}")
        index_lines.append("")
    overview = KnowledgePage(
        type="overview",
        title=project.title,
        slug="overview",
        body=(
            f"# {project.title}\n\n"
            f"This knowledge project contains {len(compiled)} compiled pages from "
            f"{len(project.sources)} scanned source files.\n\n"
            "Pages are generated from a typed Knowledge IR and retain source paths "
            "for later review."
        ),
        related=[page.slug for page in compiled[:100]],
        sources=[source.relative_path for source in project.sources[:100]],
    )
    store.write_page(project_id, overview, render_page(overview))
    # The reference wiki treats index.md as a plain navigational document;
    # typed pages retain frontmatter, but index is intentionally readable as-is.
    store._write_text(
        store.wiki_root(project_id) / "index.md",
        "\n".join(index_lines).rstrip() + "\n",
    )

    graph_nodes = [
        {"id": page.slug, "type": page.type, "title": page.title}
        for page in compiled
    ]
    known_slugs = {page.slug for page in compiled}
    graph_edges: list[dict[str, Any]] = []
    for page in compiled:
        for target in page.related:
            graph_edges.append({
                "source": page.slug,
                "target": _canonical_slug(target, known_slugs),
                "relation": "related",
            })
    for relation in relations:
        if relation.source and relation.target:
            graph_edges.append({
                **relation.to_dict(),
                "source": _canonical_slug(relation.source, known_slugs),
                "target": _canonical_slug(relation.target, known_slugs),
            })
    graph = {"version": 1, "nodes": graph_nodes, "edges": graph_edges}
    graph_path = store.project_path(project_id) / "knowledge" / "graph" / "graph.json"
    store._write_json(graph_path, graph)

    project.phase = "compiled"
    project.page_count = len(compiled)
    project.relation_count = len(graph_edges)
    project.updated_at = datetime.now(timezone.utc).isoformat()
    project.metadata["last_compile"] = project.updated_at
    store.save_project(project)
    _append_log(store, project_id, ir_values, compiled)
    return {
        "project": project.to_dict(),
        "pages": [page.to_dict() for page in compiled],
        "graph": {"nodes": len(graph_nodes), "edges": len(graph_edges)},
        "graph_path": graph_path.relative_to(store.project_path(project_id)).as_posix(),
    }


def _append_log(
    store: KnowledgeStore,
    project_id: str,
    ir_values: list[KnowledgeIR],
    pages: list[KnowledgePage],
) -> None:
    project = store.get_project(project_id)
    path = store.wiki_root(project_id) / "log.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else "# Knowledge Log\n"
    lines = [existing.rstrip()]
    date_header = f"## [{_today()}]"
    if date_header not in existing:
        lines.extend(["", date_header])
    for ir in ir_values:
        matches = [page for page in pages if page.source_path == ir.source_path]
        labels = ", ".join(f"[[{page.slug}]] ({page.type})" for page in matches[:30])
        source = next((item for item in project.sources if item.relative_path == ir.source_path), None)
        source_ref = (
            f"raw/{source.raw_relative_path}"
            if source is not None and source.raw_relative_path
            else f"raw/sources/{ir.source_path}"
        )
        entry = f"- Ingest: `{source_ref}` → created {labels or '(no pages)'}"
        if entry not in existing:
            lines.append(entry)
    store._write_text(path, "\n".join(lines).rstrip() + "\n")


def validate_project(store: KnowledgeStore, project_id: str) -> dict[str, Any]:
    project = store.get_project(project_id)
    wiki_root = store.wiki_root(project_id)
    issues: list[dict[str, Any]] = []
    pages: list[tuple[Path, str]] = []
    if wiki_root.exists():
        pages = [
            (path, path.read_text(encoding="utf-8"))
            for path in wiki_root.rglob("*.md")
            if path.name not in {"index.md", "log.md"}
        ]
    known_slugs: set[str] = set()
    source_paths = {source.relative_path for source in project.sources}
    issues.extend(_extraction_conflicts(store, project_id))
    for path, content in pages:
        metadata, _ = parse_frontmatter(content)
        page_type = str(metadata.get("type") or "")
        title = str(metadata.get("title") or "")
        slug = path.stem
        known_slugs.add(slug)
        if page_type not in PAGE_TYPES:
            issues.append({"kind": "frontmatter", "path": str(path), "message": "invalid or missing type"})
        if not title:
            issues.append({"kind": "frontmatter", "path": str(path), "message": "missing title"})
        for key in ("tags", "related", "sources", "created", "updated"):
            if key not in metadata:
                issues.append({"kind": "frontmatter", "path": str(path), "message": f"missing {key}"})
        if page_type == "source":
            for key in ("authors", "year", "url", "venue"):
                if key not in metadata:
                    issues.append({"kind": "frontmatter", "path": str(path), "message": f"missing {key}"})
        if page_type in {"entity", "concept", "source"} and not metadata.get("sources"):
            issues.append({"kind": "evidence", "path": str(path), "message": "page has no source evidence"})
        if isinstance(metadata.get("sources"), list):
            for source in metadata["sources"]:
                if source_paths and source not in source_paths:
                    issues.append({
                        "kind": "evidence",
                        "path": str(path),
                        "message": f"unknown source evidence: {source}",
                    })
    for path, content in pages:
        for target in _WIKILINK_RE.findall(content):
            target_slug = _canonical_slug(target.strip(), known_slugs)
            if target_slug not in known_slugs:
                issues.append({"kind": "wikilink", "path": str(path), "message": f"missing target [[{target}]]"})
    for path, content in pages:
        metadata, _ = parse_frontmatter(content)
        related = metadata.get("related") if isinstance(metadata.get("related"), list) else []
        for target in related:
            target_slug = _canonical_slug(str(target), known_slugs)
            if target_slug not in known_slugs:
                issues.append({
                    "kind": "wikilink",
                    "path": str(path),
                    "message": f"missing related target [[{target}]]",
                })
    graph_path = store.project_path(project_id) / "knowledge" / "graph" / "graph.json"
    if graph_path.exists():
        try:
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            graph = {}
            issues.append({"kind": "graph", "path": str(graph_path), "message": "graph.json is unreadable"})
        graph_values = graph if isinstance(graph, dict) else {}
        graph_nodes = {
            node.get("id")
            for node in graph_values.get("nodes", [])
            if isinstance(node, dict) and isinstance(node.get("id"), str)
        }
        for edge in graph_values.get("edges", []):
            if not isinstance(edge, dict):
                continue
            source = edge.get("source")
            target = edge.get("target")
            if source not in graph_nodes or target not in graph_nodes:
                issues.append({
                    "kind": "graph",
                    "path": str(graph_path),
                    "message": f"edge endpoint missing: {source} -> {target}",
                })
            if edge.get("relation") != "related" and not edge.get("evidence"):
                issues.append({
                    "kind": "evidence",
                    "path": str(graph_path),
                    "message": f"relation has no evidence: {source} -> {target}",
                })
    project.phase = "validated" if not issues else "validation_failed"
    project.metadata["last_validation"] = {
        "passed": not issues,
        "issue_count": len(issues),
        "checked_pages": len(pages),
    }
    project.updated_at = datetime.now(timezone.utc).isoformat()
    store.save_project(project)
    return {
        "project_id": project_id,
        "passed": not issues,
        "checked_pages": len(pages),
        "issues": issues[:200],
        "issue_count": len(issues),
    }
