"""Resumable source normalization before model-driven Knowledge extraction."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, cast

from nanobot.knowledge.docx_extract import extract_docx_structured
from nanobot.knowledge.image_tagging import classify_knowledge_image
from nanobot.knowledge.ingest import read_image_vision_ocr, read_pdf_pages
from nanobot.knowledge.legacy_doc import recover_legacy_doc_assets, recover_legacy_doc_text
from nanobot.knowledge.ocr import ocr_image


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _write_image_catalog(
    output: Path,
    assets: list[dict[str, Any]],
    ocr_results: list[dict[str, Any]],
) -> dict[str, str]:
    """Write a bounded, agent-readable catalog without embedding image bytes."""

    by_path = {str(item.get("path") or ""): item for item in ocr_results}
    rows: list[dict[str, Any]] = []
    for asset in assets:
        relative = str(asset.get("path") or "")
        ocr = by_path.get(relative, {})
        rows.append({
            "asset_id": str(ocr.get("asset_id") or asset.get("id") or ""),
            "path": relative,
            "status": "processed" if ocr else "pending",
            "document_type": str(ocr.get("document_type") or "unclassified"),
            "tags": list(ocr.get("tags") or []),
            "entities": list(ocr.get("entities") or []),
            "confidence": ocr.get("confidence"),
            "sensitive": bool(ocr.get("sensitive", False)),
            "requires_vision": bool(ocr.get("requires_vision", not bool(ocr))),
            "review_status": str(ocr.get("review_status") or "pending"),
            "label_method": str(ocr.get("label_method") or "none"),
            "ocr_excerpt": str(ocr.get("text") or "")[:500],
        })
    jsonl_path = output / "image-catalog.jsonl"
    jsonl_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    markdown_path = output / "image-catalog.md"
    lines = [
        "# Image evidence catalog",
        "",
        "OCR-derived labels are candidates and require evidence review before becoming claims.",
        "",
        "| Asset | Type | Tags | OCR confidence | State | Sensitive |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for row in rows:
        confidence = row["confidence"]
        confidence_text = f"{float(confidence):.3f}" if isinstance(confidence, (int, float)) else "-"
        lines.append(
            f"| `{row['path']}` | {row['document_type']} | "
            f"{', '.join(str(tag) for tag in row['tags']) or '-'} | {confidence_text} | "
            f"{row['review_status']} | {'yes' if row['sensitive'] else 'no'} |"
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"jsonl": jsonl_path.name, "markdown": markdown_path.name}


def _ocr_assets(
    root: Path,
    assets: list[dict[str, Any]],
    *,
    limit: int,
    previous: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Advance OCR by at most ``limit`` new assets and retain prior results."""

    asset_by_path = {str(item.get("path") or ""): item for item in assets}
    results: list[dict[str, Any]] = []
    for previous_item in previous or []:
        enriched = dict(previous_item)
        relative = str(enriched.get("path") or "")
        raw_confidence = enriched.get("confidence")
        confidence = float(raw_confidence) if isinstance(raw_confidence, (int, float)) else None
        enriched.update(classify_knowledge_image(
            text=str(enriched.get("text") or ""),
            confidence=confidence,
            asset=asset_by_path.get(relative),
        ))
        results.append(enriched)
    completed = {str(item.get("path") or "") for item in results}
    remaining = [item for item in assets if str(item.get("path") or "") not in completed]
    for item in remaining[: max(0, limit)]:
        relative = str(item.get("path") or "")
        path = root / relative
        if not path.is_file():
            continue
        result = ocr_image(path)
        labels = classify_knowledge_image(
            text=result.text,
            confidence=result.confidence,
            asset=item,
        )
        results.append({
            "asset_id": item.get("id"),
            "path": relative,
            "text": result.text[:80_000],
            "engine": result.engine,
            "confidence": result.confidence,
            "available": result.available,
            "error": result.error,
            **labels,
        })
    return results


def normalize_source(
    source: str | Path,
    out_dir: str | Path,
    *,
    max_ocr_assets: int = 200,
    max_legacy_assets: int = 20_000,
    start_page: int | None = None,
    end_page: int | None = None,
) -> dict[str, Any]:
    """Normalize one source into text/assets/OCR manifests.

    The function writes only derived artifacts under ``out_dir``.  OCR is
    deliberately bounded; unprocessed assets remain listed with a
    ``requires_vision`` path so a later task can resume or ask the user which
    evidence matters.
    """

    input_path = Path(source).resolve()
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    suffix = input_path.suffix.lower()
    source_hash = _sha256(input_path)
    manifest_path = output / "normalization.json"
    previous = _read_manifest(manifest_path)
    can_resume = (
        previous.get("sha256") == source_hash
        and previous.get("source_size") == input_path.stat().st_size
    )
    base: dict[str, Any] = {
        "version": 1,
        "source_path": input_path.as_posix(),
        "source_size": input_path.stat().st_size,
        "sha256": source_hash,
        "status": "processing",
        "text": "",
        "assets": [],
        "ocr": [],
        "warnings": [],
    }
    if can_resume:
        for key in ("adapter", "text", "text_segments", "assets", "tables", "formulas", "legacy_doc", "ocr"):
            if key in previous:
                base[key] = previous[key]
        base["warnings"] = list(previous.get("warnings") or [])
        base["resumed"] = True
    _write_json(manifest_path, base)
    try:
        if suffix == ".docx":
            extracted = extract_docx_structured(input_path, output)
            base["adapter"] = "docx_structured"
            base["text"] = "text.md"
            base["assets"] = extracted.get("images", [])
            base["tables"] = extracted.get("tables", [])
            base["formulas"] = extracted.get("formulas", [])
            base["warnings"].extend(extracted.get("errors", []))
        elif suffix == ".doc":
            existing_assets = base.get("assets") if can_resume else None
            if isinstance(existing_assets, list) and existing_assets and (output / "text.md").is_file():
                base["adapter"] = "legacy_doc_ole_recovery"
            else:
                if max_legacy_assets == 0:
                    raise ValueError(
                        "max_legacy_assets=0 is resume-only, but no reusable legacy DOC assets exist"
                    )
                text, metadata = recover_legacy_doc_text(input_path)
                (output / "text.md").write_text(text + "\n", encoding="utf-8")
                recovered = recover_legacy_doc_assets(
                    input_path,
                    output / "images",
                    max_assets=max_legacy_assets,
                )
                assets: list[dict[str, Any]] = []
                recovered_assets = recovered.get("assets")
                for raw_item in cast(list[Any], recovered_assets) if isinstance(recovered_assets, list) else []:
                    if not isinstance(raw_item, dict):
                        continue
                    value = dict(cast(dict[str, Any], raw_item))
                    value["path"] = Path(str(value.get("path", ""))).relative_to(output).as_posix()
                    assets.append(value)
                base.update({
                    "adapter": "legacy_doc_ole_recovery",
                    "text": "text.md",
                    "assets": assets,
                    "legacy_doc": metadata,
                    "warnings": list(recovered.get("limitations", [])),
                })
        elif suffix == ".pdf":
            first = max(1, start_page or 1)
            last = max(first, end_page or first + 4)
            read = read_pdf_pages(input_path, start_page=first, end_page=last)
            pages_dir = output / "pages"
            pages_dir.mkdir(parents=True, exist_ok=True)
            actual_last = read.end_page or last
            segment = pages_dir / f"pages-{first:06d}-{actual_last:06d}.md"
            segment.write_text(read.text + "\n", encoding="utf-8")
            raw_segments = base.get("text_segments")
            segments: dict[str, dict[str, Any]] = {}
            for raw_item in cast(list[Any], raw_segments) if isinstance(raw_segments, list) else []:
                if not isinstance(raw_item, dict):
                    continue
                item = cast(dict[str, Any], raw_item)
                if item.get("path"):
                    segments[str(item["path"])] = dict(item)
            relative_segment = segment.relative_to(output).as_posix()
            segments[relative_segment] = {
                "path": relative_segment,
                "start_page": first,
                "end_page": actual_last,
            }
            base.update({
                "adapter": read.adapter,
                "text": relative_segment,
                "text_segments": sorted(
                    segments.values(),
                    key=lambda item: int(item.get("start_page") or 0),
                ),
                "read_metadata": read.metadata or {},
            })
            total_pages = int((read.metadata or {}).get("total_pages") or actual_last)
            if actual_last < total_pages:
                base["warnings"].append(
                    f"PDF normalization is bounded; pages {actual_last + 1}-{total_pages} remain."
                )
        elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}:
            read = read_image_vision_ocr(input_path)
            base.update({
                "adapter": read.adapter,
                "ocr": [{
                    "asset_id": "source_image",
                    "path": input_path.as_posix(),
                    "text": read.text,
                    **(read.metadata or {}),
                }],
            })
        else:
            raise ValueError(f"structured normalization is not implemented for {suffix}")

        if base["assets"]:
            raw_prior_ocr: object = base.get("ocr") if can_resume else []
            prior_ocr = (
                [cast(dict[str, Any], item) for item in cast(list[Any], raw_prior_ocr) if isinstance(item, dict)]
                if isinstance(raw_prior_ocr, list)
                else []
            )
            base["ocr"] = _ocr_assets(
                output,
                list(base["assets"]),
                limit=max_ocr_assets,
                previous=prior_ocr,
            )
            base["image_catalog"] = _write_image_catalog(
                output,
                list(base["assets"]),
                list(base["ocr"]),
            )
            remaining = max(0, len(base["assets"]) - len(base["ocr"]))
            if remaining:
                base["warnings"].append(
                    f"{remaining} assets remain for bounded OCR/vision processing."
                )
        base["status"] = "normalized"
    except Exception as exc:
        base["status"] = "failed"
        base["error"] = f"{type(exc).__name__}: {str(exc)[:1_000]}"
        _write_json(manifest_path, base)
        raise
    _write_json(manifest_path, base)
    return base
