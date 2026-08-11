"""Bounded recovery helpers for legacy OLE2 Word ``.doc`` sources.

The implementation deliberately avoids opening a large document as one byte
array.  Text is read from the comparatively small ``WordDocument`` stream and
recoverable PNG/JPEG assets are carved from the large ``Data`` stream in fixed
chunks.  This is a recovery adapter, not a complete MS-DOC renderer; every
result records the method and limitations so it is never mistaken for a
layout-perfect conversion.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO, Iterable


@dataclass(frozen=True)
class RecoveredAsset:
    id: str
    path: str
    media_type: str
    stream_offset: int
    size: int
    sha256: str
    extraction_method: str = "ole_data_signature_carve"


def _ole(path: Path):
    try:
        import olefile
    except ImportError as exc:  # pragma: no cover - optional dependency gap
        raise RuntimeError("olefile is required for legacy .doc recovery") from exc
    if not olefile.isOleFile(str(path)):
        raise ValueError(f"not an OLE2 document: {path}")
    return olefile.OleFileIO(str(path))


def _clean_legacy_text(value: str) -> str:
    kept: list[str] = []
    for character in value:
        codepoint = ord(character)
        if character in {"\r", "\n", "\t"} or codepoint >= 0x20 and not 0x7F <= codepoint < 0xA0:
            kept.append(character)
    text = "".join(kept).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{4,}", "\n\n\n", text).strip()


def recover_legacy_doc_text(path: str | Path) -> tuple[str, dict[str, object]]:
    """Recover the main text span using FIB offsets without reading ``Data``."""

    source = Path(path)
    with _ole(source) as container:
        if not container.exists("WordDocument"):
            raise ValueError("legacy DOC has no WordDocument stream")
        stream = container.openstream("WordDocument")
        header = stream.read(32)
        if len(header) < 32 or header[:2] != b"\xec\xa5":
            raise ValueError("unsupported or damaged WordDocument FIB")
        fib_version = struct.unpack_from("<H", header, 2)[0]
        flags = struct.unpack_from("<H", header, 10)[0]
        fc_min = struct.unpack_from("<I", header, 24)[0]
        fc_max = struct.unpack_from("<I", header, 28)[0]
        stream_size = container.get_size("WordDocument")
        if fc_min >= fc_max or fc_max > stream_size:
            raise ValueError("invalid legacy DOC text span")
        stream.seek(fc_min)
        payload = stream.read(fc_max - fc_min)

    # Most Chinese WPS/Word 97+ simple spans are UTF-16LE.  Complex documents
    # can use a piece table; this fallback intentionally reports that limit.
    text = _clean_legacy_text(payload.decode("utf-16-le", errors="replace"))
    return text, {
        "format": "ole2_doc",
        "fib_version": fib_version,
        "flags": flags,
        "complex_document": bool(flags & 0x0004),
        "encrypted_flag": bool(flags & 0x0100),
        "word_document_size": stream_size,
        "fc_min": fc_min,
        "fc_max": fc_max,
        "recovery_method": "fib_text_span_utf16le",
        "layout_preserved": False,
    }


_FORMATS = (
    ("png", "image/png", b"\x89PNG\r\n\x1a\n", b"IEND\xaeB`\x82"),
    ("jpg", "image/jpeg", b"\xff\xd8\xff", b"\xff\xd9"),
)


def _find_start(data: bytes) -> tuple[int, tuple[str, str, bytes, bytes]] | None:
    matches = [(data.find(start), item) for item in _FORMATS for start in [item[2]]]
    matches = [item for item in matches if item[0] >= 0]
    return min(matches, key=lambda item: item[0]) if matches else None


def _write_asset(
    output: Path,
    *,
    sequence: int,
    extension: str,
    media_type: str,
    offset: int,
    payload: bytes,
) -> RecoveredAsset:
    digest = hashlib.sha256(payload).hexdigest()
    asset_id = f"asset_{sequence:06d}"
    path = output / f"{asset_id}.{extension}"
    path.write_bytes(payload)
    return RecoveredAsset(asset_id, path.as_posix(), media_type, offset, len(payload), digest)


def _carve_stream(
    stream: BinaryIO,
    output: Path,
    *,
    chunk_size: int,
    max_asset_bytes: int,
    max_assets: int,
) -> Iterable[RecoveredAsset]:
    buffer = b""
    buffer_offset = 0
    sequence = 0
    while sequence < max_assets:
        chunk = stream.read(chunk_size)
        if chunk:
            buffer += chunk
        elif not buffer:
            break

        while sequence < max_assets:
            found = _find_start(buffer)
            if found is None:
                keep = max(len(item[2]) for item in _FORMATS) - 1
                if len(buffer) > keep:
                    buffer_offset += len(buffer) - keep
                    buffer = buffer[-keep:]
                break
            start_at, item = found
            extension, media_type, _, terminator = item
            if start_at:
                buffer_offset += start_at
                buffer = buffer[start_at:]
            end_at = buffer.find(terminator, len(item[2]))
            if end_at < 0:
                if len(buffer) > max_asset_bytes:
                    # False signature or pathological object. Skip one byte and
                    # continue without retaining a huge in-memory candidate.
                    buffer = buffer[1:]
                    buffer_offset += 1
                break
            end_at += len(terminator)
            payload = buffer[:end_at]
            sequence += 1
            yield _write_asset(
                output,
                sequence=sequence,
                extension=extension,
                media_type=media_type,
                offset=buffer_offset,
                payload=payload,
            )
            buffer = buffer[end_at:]
            buffer_offset += end_at
        if not chunk:
            break


def recover_legacy_doc_assets(
    path: str | Path,
    out_dir: str | Path,
    *,
    chunk_size: int = 4 * 1024 * 1024,
    max_asset_bytes: int = 64 * 1024 * 1024,
    max_assets: int = 20_000,
) -> dict[str, Any]:
    """Stream the DOC ``Data`` OLE stream and persist recoverable assets."""

    source = Path(path)
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    with _ole(source) as container:
        if not container.exists("Data"):
            assets: list[RecoveredAsset] = []
            data_size = 0
        else:
            data_size = container.get_size("Data")
            stream = container.openstream("Data")
            assets = list(_carve_stream(
                stream,
                output,
                chunk_size=max(64 * 1024, chunk_size),
                max_asset_bytes=max(1 * 1024 * 1024, max_asset_bytes),
                max_assets=max(1, max_assets),
            ))
    manifest: dict[str, Any] = {
        "version": 1,
        "source_path": source.as_posix(),
        "source_size": source.stat().st_size,
        "data_stream_size": data_size,
        "method": "ole_data_signature_carve",
        "limitations": [
            "Only directly embedded PNG/JPEG signatures are recovered.",
            "WMF/EMF/OLE package objects and layout positions require a full DOC renderer.",
        ],
        "assets": [asdict(asset) for asset in assets],
    }
    manifest_path = output.parent / "legacy-doc-assets.json"
    temporary = manifest_path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(manifest_path)
    return manifest
