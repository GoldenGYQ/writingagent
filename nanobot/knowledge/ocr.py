"""Optional local OCR with explicit engine/fallback metadata."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Protocol, cast


class _TesseractModule(Protocol):
    def image_to_string(self, image: object, *, lang: str) -> str: ...


@dataclass(frozen=True)
class OCRResult:
    text: str
    engine: str
    confidence: float | None
    available: bool
    error: str = ""

    def metadata(self) -> dict[str, Any]:
        return {
            "ocr_available": self.available,
            "ocr_engine": self.engine,
            "ocr_confidence": self.confidence,
            "ocr_error": self.error,
        }


@lru_cache(maxsize=1)
def _rapidocr_engine():
    from rapidocr import RapidOCR

    return RapidOCR()


def _rapidocr(path: Path) -> OCRResult:
    result: object = _rapidocr_engine()(str(path))
    texts: list[str] = []
    scores: list[float] = []
    # RapidOCR 3.x returns an object; older compatible releases returned a
    # (result, elapsed) tuple.  Supporting both keeps the optional adapter
    # independent from one patch release.
    if hasattr(result, "txts"):
        raw_texts = getattr(result, "txts", None)
        raw_scores = getattr(result, "scores", None)
        texts = [str(value) for value in cast(Iterable[object], raw_texts or [])]
        scores = [float(value) for value in cast(Iterable[float], raw_scores or [])]
    elif isinstance(result, tuple) and result:
        tuple_result = cast(tuple[object, ...], result)
        rows: object = tuple_result[0]
        for row in cast(Iterable[object], rows or []):
            if isinstance(row, (list, tuple)):
                values = cast(list[object] | tuple[object, ...], row)
                if len(values) < 3:
                    continue
                texts.append(str(values[1]))
                try:
                    scores.append(float(cast(Any, values[2])))
                except (TypeError, ValueError):
                    pass
    confidence = sum(scores) / len(scores) if scores else None
    return OCRResult("\n".join(texts).strip(), "rapidocr", confidence, True)


def _tesseract(path: Path) -> OCRResult:
    import pytesseract  # pyright: ignore[reportMissingImports]
    from PIL import Image

    with Image.open(path) as image:
        engine = cast(_TesseractModule, pytesseract)
        text = engine.image_to_string(image, lang="chi_sim+eng").strip()
    return OCRResult(text, "tesseract", None, True)


def ocr_image(path: str | Path, *, engine: str = "auto") -> OCRResult:
    """OCR one image without inventing text when no engine is available."""

    source = Path(path)
    errors: list[str] = []
    engines = ("rapidocr", "tesseract") if engine == "auto" else (engine,)
    for candidate in engines:
        try:
            if candidate == "rapidocr":
                return _rapidocr(source)
            if candidate == "tesseract":
                return _tesseract(source)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            errors.append(f"{candidate}: {str(exc)[:300]}")
    return OCRResult("", "none", None, False, "; ".join(errors))
