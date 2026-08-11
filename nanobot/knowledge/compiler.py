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
    KnowledgeEvidence,
    KnowledgeIR,
    KnowledgePage,
    KnowledgeProject,
    KnowledgeRelation,
)
from nanobot.knowledge.store import KnowledgeStore

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_INVALID_SLUG_CHARS = re.compile(r"[\\/:*?\"<>|\x00-\x1f]+")
_MULTISPACE_RE = re.compile(r"\s+")
_MIN_SUBSTANTIVE_BODY_CHARS = 80


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


def _canonical_page_ref(
    value: str,
    known_slugs: set[str],
    title_to_slug: dict[str, str] | None = None,
) -> str:
    """Resolve a page reference supplied as either a title or a slug.

    LLM extraction naturally tends to use the human-readable page title in
    ``related`` and relation endpoints, while the filesystem and graph use a
    stable slug.  Keeping the title-to-slug mapping at compile/validation time
    lets the IR accept both forms without weakening missing-target checks.
    """
    clean = str(value or "").strip()
    if title_to_slug:
        mapped = title_to_slug.get(clean.casefold())
        if mapped:
            return mapped
        mapped = title_to_slug.get(safe_slug(clean).casefold())
        if mapped:
            return mapped
    return _canonical_slug(clean, known_slugs)


def _frontmatter_list(value: Any) -> str:
    values = cast(list[Any], value) if isinstance(value, list) else []
    return json.dumps(_unique([str(item) for item in values if str(item).strip()]), ensure_ascii=False)


def _frontmatter_evidence(value: Any) -> str:
    if not isinstance(value, list):
        return "[]"
    compact: list[dict[str, Any]] = []
    for item in cast(list[Any], value)[:20]:
        if not isinstance(item, KnowledgeEvidence):
            continue
        compact.append({
            key: candidate
            for key, candidate in item.to_dict().items()
            if candidate not in (None, "", [], {})
        })
    return json.dumps(compact, ensure_ascii=False)


def render_page(page: KnowledgePage) -> str:
    """Render one page using the reference wiki frontmatter contract."""
    frontmatter_lines = [
        "---",
        f"type: {page.type}",
        f"title: {json.dumps(page.title, ensure_ascii=False)}",
        f"tags: {_frontmatter_list(page.tags)}",
        f"related: {_frontmatter_list(page.related)}",
        f"sources: {_frontmatter_list(page.sources)}",
        f"evidence: {_frontmatter_evidence(page.evidence)}",
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
                        {
                            str(item_key): item_value
                            for item_key, item_value in cast(dict[Any, Any], item).items()
                        } if isinstance(item, dict) else str(item)
                        for item in cast(list[Any], value)
                        if item is not None
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


def _substantive_body(body: str) -> str:
    """Return body text used by the publish quality gate.

    Compiled pages always receive a title heading, so counting the heading
    would allow an otherwise empty page to pass validation.  Markdown syntax
    is intentionally only lightly normalized here: the gate is checking that
    the extraction contains enough human-readable substance, not validating
    Markdown itself.
    """
    lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _page_path_at(root: Path, page: KnowledgePage) -> Path:
    directory = {
        "entity": "entities",
        "concept": "concepts",
        "source": "sources",
        "query": "queries",
        "comparison": "comparisons",
        "synthesis": "synthesis",
        "overview": "",
    }.get(page.type)
    if directory is None:
        raise ValueError(f"unsupported knowledge page type: {page.type}")
    return root / directory / f"{page.slug}.md" if directory else root / f"{page.slug}.md"


def _merge_existing(
    store: KnowledgeStore,
    project_id: str,
    page: KnowledgePage,
    *,
    existing_root: Path | None = None,
) -> KnowledgePage:
    """Merge a new extraction into an existing page instead of overwriting it."""
    path = (
        _page_path_at(existing_root, page)
        if existing_root is not None
        else store.page_path(project_id, page.type, page.slug)
    )
    if not path.exists():
        return page
    try:
        metadata, old_body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return page
    raw_old_tags = metadata.get("tags")
    raw_old_related = metadata.get("related")
    raw_old_sources = metadata.get("sources")
    old_tags = cast(list[Any], raw_old_tags) if isinstance(raw_old_tags, list) else []
    old_related = cast(list[Any], raw_old_related) if isinstance(raw_old_related, list) else []
    old_sources = cast(list[Any], raw_old_sources) if isinstance(raw_old_sources, list) else []
    raw_old_evidence = metadata.get("evidence")
    old_evidence = [
        KnowledgeEvidence.from_dict(cast(dict[str, Any], item))
        for item in cast(list[Any], raw_old_evidence)
        if isinstance(item, dict)
    ] if isinstance(raw_old_evidence, list) else []
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
        evidence=[*old_evidence, *page.evidence],
    )


def _page_from_entity(entity: dict[str, Any], source_path: str) -> KnowledgePage | None:
    name = str(entity.get("name") or "").strip()
    if not name:
        return None
    raw_tags = entity.get("tags")
    tags = [
        str(value).strip()
        for value in cast(list[Any], raw_tags)
        if str(value).strip()
    ] if isinstance(raw_tags, list) else []
    raw_related = entity.get("related")
    related = [
        str(value).strip()
        for value in cast(list[Any], raw_related)
        if str(value).strip()
    ] if isinstance(raw_related, list) else []
    raw_evidence = entity.get("evidence")
    return KnowledgePage(
        type=str(entity.get("type") or "entity"),
        title=name,
        slug=safe_slug(name),
        body=str(entity.get("description") or ""),
        tags=tags,
        related=related,
        source_path=source_path,
        sources=[source_path] if source_path else [],
        evidence=[
            KnowledgeEvidence.from_dict(cast(dict[str, Any], item))
            for item in cast(list[Any], raw_evidence)
            if isinstance(item, dict)
        ] if isinstance(raw_evidence, list) else [],
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


_COMMUNITY_COLORS = (
    "#5B9CF6",
    "#8B6CF6",
    "#F59E0B",
    "#10B981",
    "#EF6C8C",
    "#14B8A6",
    "#F97316",
    "#64748B",
)


def _topology_graph(
    raw_nodes: list[dict[str, Any]],
    raw_edges: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize graph topology and compute deterministic lightweight communities.

    The runtime deliberately avoids a mandatory graph dependency.  A stable
    label-propagation pass over the undirected graph gives useful topology
    communities while remaining deterministic for the same IR.  It also has a
    predictable fallback for isolated and single-node graphs.
    """
    node_map: dict[str, dict[str, Any]] = {}
    for node in raw_nodes:
        node_id = str(node.get("id") or "").strip()
        if node_id and node_id not in node_map:
            node_map[node_id] = dict(node)
    edge_map: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_edge in raw_edges:
        source = str(raw_edge.get("source") or "").strip()
        target = str(raw_edge.get("target") or "").strip()
        if not source or not target or source == target:
            continue
        if source not in node_map or target not in node_map:
            continue
        source, target = sorted((source, target), key=str.casefold)
        key = (source, target)
        current = edge_map.get(key)
        relation = str(raw_edge.get("relation") or "related")
        if current is None:
            current = cast(dict[str, Any], {"source": source, "target": target, "relation": relation})
            edge_map[key] = current
        else:
            current_relation = str(current.get("relation") or "related")
            if current_relation == "related" and relation != "related":
                current["relation"] = relation
            elif relation != "related" and current_relation != relation:
                labels = {
                    item.strip()
                    for item in f"{current_relation} · {relation}".split("·")
                    if item.strip()
                }
                current["relation"] = " · ".join(sorted(labels, key=str.casefold))
        evidence = raw_edge.get("evidence")
        if evidence and not current.get("evidence"):
            current["evidence"] = evidence
        evidence_refs = raw_edge.get("evidence_refs")
        if isinstance(evidence_refs, list):
            existing_refs: Any = current.setdefault("evidence_refs", [])
            if isinstance(existing_refs, list):
                typed_existing = cast(list[Any], existing_refs)
                for item in cast(list[Any], evidence_refs):
                    if isinstance(item, dict) and item not in typed_existing and len(typed_existing) < 20:
                        typed_existing.append(item)
        confidence = raw_edge.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            current["confidence"] = max(float(confidence), float(current.get("confidence", 0)))

    node_ids = sorted(node_map, key=str.casefold)
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for source, target in edge_map:
        adjacency[source].add(target)
        adjacency[target].add(source)

    labels = {node_id: node_id for node_id in node_ids}
    for _ in range(20):
        changed = False
        order = sorted(node_ids, key=lambda item: (-len(adjacency[item]), item.casefold()))
        for node_id in order:
            neighbors = adjacency[node_id]
            if not neighbors:
                continue
            counts: dict[str, int] = {}
            for neighbor in neighbors:
                label = labels[neighbor]
                counts[label] = counts.get(label, 0) + 1
            best = min(
                counts,
                key=lambda label: (-counts[label], label.casefold()),
            )
            if best != labels[node_id]:
                labels[node_id] = best
                changed = True
        if not changed:
            break

    groups: dict[str, list[str]] = defaultdict(list)
    for node_id, label in labels.items():
        groups[label].append(node_id)
    ordered_groups = sorted(groups.values(), key=lambda values: (-len(values), min(values).casefold()))
    community_by_node: dict[str, tuple[str, str, int, str]] = {}
    communities: list[dict[str, Any]] = []
    for index, members in enumerate(ordered_groups, start=1):
        members = sorted(members, key=str.casefold)
        community_id = f"community-{index:02d}"
        representative = max(
            members,
            key=lambda item: (len(adjacency[item]), -node_ids.index(item)),
        )
        label = str(node_map[representative].get("title") or representative)
        color = _COMMUNITY_COLORS[(index - 1) % len(_COMMUNITY_COLORS)]
        communities.append({
            "id": community_id,
            "label": label,
            "size": len(members),
            "color": color,
            "nodes": members,
        })
        for member in members:
            community_by_node[member] = (community_id, label, len(members), color)

    max_degree = max((len(neighbors) for neighbors in adjacency.values()), default=0)
    nodes: list[dict[str, Any]] = []
    for node_id in node_ids:
        community_id, community_label, community_size, color = community_by_node[node_id]
        degree = len(adjacency[node_id])
        nodes.append({
            **node_map[node_id],
            "community_id": community_id,
            "community_label": community_label,
            "community_size": community_size,
            "centrality": round(degree / max_degree, 4) if max_degree else 0.0,
            "degree": degree,
            "color": color,
        })
    return nodes, [edge_map[key] for key in sorted(edge_map, key=lambda item: (item[0].casefold(), item[1].casefold()))], communities


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
                tags=list(entity.tags),
                related=list(entity.related),
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


def compile_project(
    store: KnowledgeStore,
    project_id: str,
    *,
    output_root: Path | None = None,
    existing_root: Path | None = None,
    graph_path: Path | None = None,
) -> dict[str, Any]:
    project = store.get_project(project_id)
    wiki_root = output_root or store.wiki_root(project_id)
    existing_root = existing_root or store.wiki_root(project_id)
    graph_path = graph_path or (store.project_path(project_id) / "knowledge" / "graph" / "graph.json")
    ir_values = store.list_ir(project_id)
    page_map: dict[tuple[str, str], KnowledgePage] = {}
    title_keys: dict[tuple[str, str], tuple[str, str]] = {}
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
            title_key = (page.type, page.title.strip().casefold())
            existing_key = title_keys.get(title_key)
            if existing_key is not None and existing_key != key:
                current = page_map[existing_key]
                current.tags = _unique([*current.tags, *page.tags])
                current.related = _unique([*current.related, *page.related])
                current.sources = _unique([*current.sources, *page.sources])
                current.evidence = [*current.evidence, *page.evidence]
                if page.body.strip() and page.body.strip() not in current.body:
                    current.body = f"{current.body.rstrip()}\n\n{page.body.strip()}"
                continue
            page_map[key] = _merge_existing(
                store,
                project_id,
                page,
                existing_root=existing_root,
            )
            title_keys[title_key] = key
        relations.extend(ir.relations)

    compiled: list[KnowledgePage] = list(page_map.values())
    # Materialize relation targets into page frontmatter as well as graph.json.
    # This keeps the page useful when opened without rendering the graph and
    # prevents valid entity relationships from disappearing as empty metadata.
    pages_by_slug = {page.slug: page for page in compiled}
    known_slugs = set(pages_by_slug)
    title_to_slug = {
        key: page.slug
        for page in compiled
        for key in (page.title.casefold(), safe_slug(page.title).casefold(), page.slug.casefold())
    }
    for relation in relations:
        source_slug = _canonical_page_ref(relation.source, known_slugs, title_to_slug)
        target_slug = _canonical_page_ref(relation.target, known_slugs, title_to_slug)
        source_page = pages_by_slug.get(source_slug)
        if source_page is not None and target_slug in known_slugs:
            source_page.related = _unique([*source_page.related, target_slug])
    for page in compiled:
        page.related = _unique([
            _canonical_page_ref(target, known_slugs, title_to_slug)
            for target in page.related
        ])
    for page in compiled:
        store.write_derived_text(_page_path_at(wiki_root, page), render_page(page))

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
    store.write_derived_text(_page_path_at(wiki_root, overview), render_page(overview))
    # The reference wiki treats index.md as a plain navigational document;
    # typed pages retain frontmatter, but index is intentionally readable as-is.
    store.write_derived_text(
        wiki_root / "index.md",
        "\n".join(index_lines).rstrip() + "\n",
    )

    graph_nodes = [
        {"id": page.slug, "type": page.type, "title": page.title}
        for page in compiled
    ]
    known_slugs = {page.slug for page in compiled}
    title_to_slug = {
        key: page.slug
        for page in compiled
        for key in (page.title.casefold(), safe_slug(page.title).casefold(), page.slug.casefold())
    }
    graph_edges: list[dict[str, Any]] = []
    for page in compiled:
        for target in page.related:
            graph_edges.append({
                "source": page.slug,
                "target": _canonical_page_ref(target, known_slugs, title_to_slug),
                "relation": "related",
            })
    for relation in relations:
        if relation.source and relation.target:
            graph_edges.append({
                **relation.to_dict(),
                "source": _canonical_page_ref(relation.source, known_slugs, title_to_slug),
                "target": _canonical_page_ref(relation.target, known_slugs, title_to_slug),
            })
    graph_nodes, graph_edges, communities = _topology_graph(graph_nodes, graph_edges)
    graph = {
        "version": 2,
        "directed": False,
        "nodes": graph_nodes,
        "edges": graph_edges,
        "communities": communities,
    }
    store.write_derived_json(graph_path, graph)

    project.phase = "compiled"
    project.page_count = len(compiled)
    project.relation_count = len(graph_edges)
    project.updated_at = datetime.now(timezone.utc).isoformat()
    project.metadata["last_compile"] = project.updated_at
    store.save_project(project)
    _append_log(store, project_id, ir_values, compiled, wiki_root=wiki_root)
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
    *,
    wiki_root: Path | None = None,
) -> None:
    project = store.get_project(project_id)
    path = (wiki_root or store.wiki_root(project_id)) / "log.md"
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
    store.write_derived_text(path, "\n".join(lines).rstrip() + "\n")


def validate_project(
    store: KnowledgeStore,
    project_id: str,
    *,
    wiki_root_override: Path | None = None,
    graph_path_override: Path | None = None,
) -> dict[str, Any]:
    project = store.get_project(project_id)
    wiki_root = wiki_root_override or store.wiki_root(project_id)
    graph_path = graph_path_override or (store.project_path(project_id) / "knowledge" / "graph" / "graph.json")
    issues: list[dict[str, Any]] = []
    pages: list[tuple[Path, str]] = []
    if wiki_root.exists():
        pages = [
            (path, path.read_text(encoding="utf-8"))
            for path in wiki_root.rglob("*.md")
            if path.name not in {"index.md", "log.md"}
        ]
    known_slugs: set[str] = set()
    title_to_slug: dict[str, str] = {}
    slug_paths: dict[str, str] = {}
    source_paths = {source.relative_path for source in project.sources}
    if len(source_paths) != len(project.sources):
        issues.append({
            "kind": "duplicate",
            "path": f"wikis/{project_id}/project.json",
            "message": "duplicate source paths in the Knowledge manifest",
        })
    issues.extend(_extraction_conflicts(store, project_id))
    for ir in store.list_ir(project_id):
        for relation in ir.relations:
            if relation.source_path and source_paths and relation.source_path not in source_paths:
                issues.append({
                    "kind": "evidence",
                    "path": f"knowledge/ir/{ir.source_path}",
                    "message": f"unknown relation source evidence: {relation.source_path}",
                })
            has_start = relation.start_line is not None
            has_end = relation.end_line is not None
            if has_start != has_end:
                issues.append({
                    "kind": "evidence",
                    "path": f"knowledge/ir/{ir.source_path}",
                    "message": "relation evidence must include both start_line and end_line",
                })
            elif has_start and (
                relation.start_line is None
                or relation.end_line is None
                or relation.start_line < 1
                or relation.end_line < relation.start_line
            ):
                issues.append({
                    "kind": "evidence",
                    "path": f"knowledge/ir/{ir.source_path}",
                    "message": "relation evidence line range is invalid",
                })
            if relation.source and relation.target and relation.source == relation.target:
                issues.append({
                    "kind": "graph",
                    "path": f"knowledge/ir/{ir.source_path}",
                    "message": "self-loop relation is not publishable",
                })
            for evidence in relation.evidence_refs:
                if evidence.source_path and source_paths and evidence.source_path not in source_paths:
                    issues.append({
                        "kind": "evidence",
                        "path": f"knowledge/ir/{ir.source_path}",
                        "message": f"unknown relation evidence source: {evidence.source_path}",
                    })
                if evidence.start_line is not None and evidence.end_line is not None and evidence.end_line < evidence.start_line:
                    issues.append({
                        "kind": "evidence",
                        "path": f"knowledge/ir/{ir.source_path}",
                        "message": "relation evidence line range is invalid",
                    })
        for claim in ir.claims:
            if not claim.subject.strip() or not claim.predicate.strip() or not claim.object.strip():
                issues.append({
                    "kind": "quality",
                    "path": f"knowledge/ir/{ir.source_path}",
                    "message": "claim subject, predicate, and object are required",
                })
            if claim.status not in {"asserted", "uncertain", "conflict", "retracted", "confirmed"}:
                issues.append({
                    "kind": "quality",
                    "path": f"knowledge/ir/{ir.source_path}",
                    "message": f"unsupported claim status: {claim.status}",
                })
            if claim.source_path and source_paths and claim.source_path not in source_paths:
                issues.append({
                    "kind": "evidence",
                    "path": f"knowledge/ir/{ir.source_path}",
                    "message": f"unknown claim source evidence: {claim.source_path}",
                })
            for evidence in claim.evidence:
                if evidence.source_path and source_paths and evidence.source_path not in source_paths:
                    issues.append({
                        "kind": "evidence",
                        "path": f"knowledge/ir/{ir.source_path}",
                        "message": f"unknown claim evidence source: {evidence.source_path}",
                    })
                if evidence.start_line is not None and evidence.end_line is not None and evidence.end_line < evidence.start_line:
                    issues.append({
                        "kind": "evidence",
                        "path": f"knowledge/ir/{ir.source_path}",
                        "message": "claim evidence line range is invalid",
                    })
        for evidence in ir.evidence:
            if evidence.source_path and source_paths and evidence.source_path not in source_paths:
                issues.append({
                    "kind": "evidence",
                    "path": f"knowledge/ir/{ir.source_path}",
                    "message": f"unknown evidence source: {evidence.source_path}",
                })
    for path, content in pages:
        metadata, body = parse_frontmatter(content)
        page_type = str(metadata.get("type") or "")
        title = str(metadata.get("title") or "")
        slug = path.stem
        if slug in slug_paths and slug_paths[slug] != str(path):
            issues.append({
                "kind": "duplicate",
                "path": str(path),
                "message": f"duplicate page slug: {slug}",
                "pages": [slug_paths[slug], str(path)],
            })
        slug_paths[slug] = str(path)
        known_slugs.add(slug)
        title_to_slug[title.casefold()] = slug
        title_to_slug[safe_slug(title).casefold()] = slug
        title_to_slug[slug.casefold()] = slug
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
        if page_type in {"entity", "concept", "source"}:
            body_text = _substantive_body(body)
            if len(body_text) < _MIN_SUBSTANTIVE_BODY_CHARS:
                issues.append({
                    "kind": "quality",
                    "path": str(path),
                    "message": (
                        "page body is empty or too short after removing the title heading "
                        f"(minimum {_MIN_SUBSTANTIVE_BODY_CHARS} characters)"
                    ),
                })
            tags = metadata.get("tags")
            if not isinstance(tags, list) or not any(
                str(tag).strip() for tag in cast(list[Any], tags)
            ):
                issues.append({
                    "kind": "quality",
                    "path": str(path),
                    "message": "page has no semantic tags",
                })
        if isinstance(metadata.get("sources"), list):
            if len({str(source) for source in metadata["sources"]}) != len(metadata["sources"]):
                issues.append({
                    "kind": "duplicate",
                    "path": str(path),
                    "message": "page contains duplicate source references",
                })
            for source in metadata["sources"]:
                if source_paths and source not in source_paths:
                    issues.append({
                        "kind": "evidence",
                        "path": str(path),
                        "message": f"unknown source evidence: {source}",
                    })
    for path, content in pages:
        for target in _WIKILINK_RE.findall(content):
            target_slug = _canonical_page_ref(target.strip(), known_slugs, title_to_slug)
            if target_slug not in known_slugs:
                issues.append({"kind": "wikilink", "path": str(path), "message": f"missing target [[{target}]]"})
    for path, content in pages:
        metadata, _ = parse_frontmatter(content)
        raw_related = metadata.get("related")
        related = cast(list[Any], raw_related) if isinstance(raw_related, list) else []
        for target in related:
            target_slug = _canonical_page_ref(str(target), known_slugs, title_to_slug)
            if target_slug not in known_slugs:
                issues.append({
                    "kind": "wikilink",
                    "path": str(path),
                    "message": f"missing related target [[{target}]]",
                })
    if graph_path.exists():
        try:
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            graph = {}
            issues.append({"kind": "graph", "path": str(graph_path), "message": "graph.json is unreadable"})
        graph_values = cast(dict[str, Any], graph) if isinstance(graph, dict) else {}
        raw_nodes = graph_values.get("nodes")
        graph_node_values = cast(list[Any], raw_nodes) if isinstance(raw_nodes, list) else []
        graph_nodes = {
            cast(dict[str, Any], node).get("id")
            for node in graph_node_values
            if isinstance(node, dict) and isinstance(cast(dict[str, Any], node).get("id"), str)
        }
        raw_edges = graph_values.get("edges")
        for raw_edge in cast(list[Any], raw_edges) if isinstance(raw_edges, list) else []:
            if not isinstance(raw_edge, dict):
                continue
            edge = cast(dict[str, Any], raw_edge)
            source = edge.get("source")
            target = edge.get("target")
            if source not in graph_nodes or target not in graph_nodes:
                issues.append({
                    "kind": "graph",
                    "path": str(graph_path),
                    "message": f"edge endpoint missing: {source} -> {target}",
                })
            if edge.get("relation") != "related" and not edge.get("evidence") and not edge.get("evidence_refs"):
                issues.append({
                    "kind": "evidence",
                    "path": str(graph_path),
                    "message": f"relation has no evidence: {source} -> {target}",
                })
        for raw_node in graph_node_values:
            if not isinstance(raw_node, dict):
                continue
            node = cast(dict[str, Any], raw_node)
            for key in ("community_id", "community_label", "community_size", "centrality", "color"):
                if key not in node:
                    issues.append({
                        "kind": "graph",
                        "path": str(graph_path),
                        "message": f"graph node missing {key}: {node.get('id')}",
                    })
    project.phase = "validated" if not issues else "validation_failed"
    project.metadata["last_validation"] = {
        "passed": not issues,
        "issue_count": len(issues),
        "checked_pages": len(pages),
        "quality_issue_count": sum(1 for issue in issues if issue.get("kind") == "quality"),
    }
    project.updated_at = datetime.now(timezone.utc).isoformat()
    store.save_project(project)
    return {
        "project_id": project_id,
        "passed": not issues,
        "checked_pages": len(pages),
        "issues": issues[:200],
        "issue_count": len(issues),
        "quality_issue_count": sum(1 for issue in issues if issue.get("kind") == "quality"),
    }
