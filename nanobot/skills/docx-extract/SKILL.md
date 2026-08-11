---
name: docx-extract
description: "Extract DOCX text, tables, OMML formulas, and embedded media into bounded evidence assets for Knowledge ingestion."
---

# Structured DOCX extraction

Use this capability when a Word source contains more than plain paragraphs,
especially tables, formulas, certificates, invoices, or photographs.

The runtime implementation is `nanobot.knowledge.docx_extract.extract_docx_structured`.
It produces `text.md`, `tables/`, `formulas/`, `images/`, and `manifest.json`.

Rules:

- Preserve the original source; generated files are derived evidence only.
- Do not inject an entire document or all images into model context.
- Read `text.md` in bounded ranges and process image assets independently.
- Use `image-catalog.md`/`image-catalog.jsonl` to select evidence by candidate
  document type and tag instead of opening every extracted image.
- Every extracted fact must cite the original source plus page/line/asset evidence.
- OCR output is a candidate transcription, not an authoritative fact.
- For legacy `.doc`, use the recovery adapter and report layout/media limitations.
