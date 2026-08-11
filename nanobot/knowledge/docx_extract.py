"""Structured, bounded DOCX extraction used by Knowledge ingestion.

This is the reusable runtime migration of the original ``docx-extract`` skill:
paragraph/table order is preserved in Markdown, tables are exported as CSV,
OMML formulas remain available as XML evidence, and media is streamed out of
the ZIP package instead of loading all images into memory.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import posixpath
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "v": "urn:schemas-microsoft-com:vml",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_MAX_XML_BYTES = 128 * 1024 * 1024
_MAX_MEDIA_BYTES = 128 * 1024 * 1024


def _q(prefix: str, tag: str) -> str:
    return f"{{{_NS[prefix]}}}{tag}"


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", Path(value).name) or "asset.bin"


def _read_member(archive: zipfile.ZipFile, name: str, *, limit: int = _MAX_XML_BYTES) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > limit:
        raise ValueError(f"DOCX member exceeds extraction limit: {name} ({info.file_size} bytes)")
    with archive.open(info) as stream:
        return stream.read(limit + 1)


def _relationships(archive: zipfile.ZipFile, part: str) -> dict[str, str]:
    rel_path = posixpath.join(posixpath.dirname(part), "_rels", f"{posixpath.basename(part)}.rels")
    try:
        root = ET.fromstring(_read_member(archive, rel_path))
    except (KeyError, ET.ParseError, ValueError):
        return {}
    return {
        str(node.get("Id")): posixpath.normpath(posixpath.join(posixpath.dirname(part), str(node.get("Target"))))
        for node in root.findall(_q("rel", "Relationship"))
        if node.get("Id") and node.get("Target")
    }


def _copy_member(archive: zipfile.ZipFile, member: str, destination: Path) -> tuple[int, str]:
    info = archive.getinfo(member)
    if info.file_size > _MAX_MEDIA_BYTES:
        raise ValueError(f"DOCX media exceeds per-asset limit: {member} ({info.file_size} bytes)")
    digest = hashlib.sha256()
    size = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(info) as source, destination.open("wb") as target:
        while block := source.read(1024 * 1024):
            size += len(block)
            digest.update(block)
            target.write(block)
    return size, digest.hexdigest()


def _text(node: ET.Element) -> str:
    values: list[str] = []
    for item in node.iter():
        if item.tag == _q("w", "t"):
            values.append(item.text or "")
        elif item.tag == _q("w", "tab"):
            values.append("\t")
        elif item.tag in {_q("w", "br"), _q("w", "cr")}:
            values.append("\n")
    return "".join(values).strip()


def _image_rids(node: ET.Element) -> list[str]:
    values = [item.get(_q("r", "embed")) for item in node.findall(".//a:blip", _NS)]
    values.extend(item.get(_q("r", "id")) for item in node.findall(".//v:imagedata", _NS))
    return [str(value) for value in values if value]


def extract_docx_structured(
    source: str | Path,
    out_dir: str | Path,
    *,
    include_headers_footers: bool = True,
) -> dict[str, Any]:
    input_path = Path(source)
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    images = output / "images"
    tables = output / "tables"
    formulas = output / "formulas"
    for directory in (images, tables, formulas):
        directory.mkdir(parents=True, exist_ok=True)

    markdown: list[str] = []
    media_manifest: list[dict[str, Any]] = []
    table_manifest: list[dict[str, Any]] = []
    formula_manifest: list[dict[str, Any]] = []
    errors: list[str] = []
    exported: dict[str, str] = {}

    with zipfile.ZipFile(input_path) as archive:
        parts = ["word/document.xml"]
        if include_headers_footers:
            parts.extend(sorted(
                name for name in archive.namelist()
                if (name.startswith("word/header") or name.startswith("word/footer")) and name.endswith(".xml")
            ))
        for part in parts:
            try:
                root = ET.fromstring(_read_member(archive, part))
            except (KeyError, ET.ParseError, ValueError) as exc:
                errors.append(f"{part}: {exc}")
                continue
            rels = _relationships(archive, part)
            body = root.find(".//w:body", _NS)
            if body is None:
                body = root
            if part != "word/document.xml":
                markdown.extend(("", f"## {Path(part).stem}", ""))
            for block in list(body):
                if block.tag == _q("w", "p"):
                    value = _text(block)
                    if value:
                        markdown.extend((value, ""))
                    for rid in _image_rids(block):
                        member = rels.get(rid)
                        if not member:
                            errors.append(f"unresolved image relationship: {part}:{rid}")
                            continue
                        if member not in exported:
                            filename = f"image_{len(exported) + 1:06d}_{_safe_name(member)}"
                            try:
                                size, digest = _copy_member(archive, member, images / filename)
                            except (KeyError, OSError, ValueError) as exc:
                                errors.append(f"{member}: {exc}")
                                continue
                            exported[member] = filename
                            media_manifest.append({
                                "id": f"image_{len(media_manifest) + 1:06d}",
                                "path": f"images/{filename}",
                                "docx_part": member,
                                "size": size,
                                "sha256": digest,
                            })
                        markdown.extend((f"![]({f'images/{exported[member]}'})", ""))
                    for formula in block.findall(".//m:oMath", _NS):
                        formula_id = f"formula_{len(formula_manifest) + 1:06d}"
                        formula_path = formulas / f"{formula_id}.omml.xml"
                        formula_path.write_bytes(ET.tostring(formula, encoding="utf-8", xml_declaration=True))
                        formula_manifest.append({"id": formula_id, "path": f"formulas/{formula_path.name}"})
                        markdown.append(f"[[FORMULA:{formula_id}]]")
                elif block.tag == _q("w", "tbl"):
                    table_id = f"table_{len(table_manifest) + 1:06d}"
                    rows = [
                        [_text(cell).replace("\n", " ") for cell in row.findall("./w:tc", _NS)]
                        for row in block.findall("./w:tr", _NS)
                    ]
                    csv_path = tables / f"{table_id}.csv"
                    with csv_path.open("w", encoding="utf-8", newline="") as handle:
                        csv.writer(handle).writerows(rows)
                    table_manifest.append({"id": table_id, "path": f"tables/{csv_path.name}", "rows": len(rows)})
                    markdown.extend((f"[[TABLE:{table_id}]]", ""))

        # Export unreferenced previews while preserving streaming limits.
        for member in sorted(name for name in archive.namelist() if name.startswith("word/media/") and not name.endswith("/")):
            if member in exported:
                continue
            filename = f"image_{len(exported) + 1:06d}_{_safe_name(member)}"
            try:
                size, digest = _copy_member(archive, member, images / filename)
            except (KeyError, OSError, ValueError) as exc:
                errors.append(f"{member}: {exc}")
                continue
            exported[member] = filename
            media_manifest.append({
                "id": f"image_{len(media_manifest) + 1:06d}",
                "path": f"images/{filename}",
                "docx_part": member,
                "size": size,
                "sha256": digest,
                "unreferenced_preview": True,
            })

    text_path = output / "text.md"
    text_path.write_text("\n".join(markdown).strip() + "\n", encoding="utf-8")
    manifest = {
        "version": 1,
        "source_path": input_path.as_posix(),
        "source_size": input_path.stat().st_size,
        "adapter": "docx_structured",
        "text": "text.md",
        "images": media_manifest,
        "tables": table_manifest,
        "formulas": formula_manifest,
        "errors": errors,
    }
    manifest_path = output / "manifest.json"
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with temporary.open("r+") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    shutil.move(str(temporary), str(manifest_path))
    return manifest
