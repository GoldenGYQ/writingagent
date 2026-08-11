---
name: knowledge-engineering
description: "Build and maintain a workspace-scoped structured knowledge wiki from source documents. Use knowledge_scan, knowledge_extract, knowledge_compile, knowledge_validate, knowledge_publish, knowledge_approve, knowledge_reject, and knowledge_search; preserve source evidence and merge existing pages."
metadata: {"nanobot":{"emoji":"📚"}}
---

# Knowledge Engineering

Use this skill when the user asks to build, update, inspect, or cite a project
knowledge base. `/knowledge [source-directory]` is an entry point into the
normal Agent Runtime; it is not a hidden workflow. If the source directory is
missing, use `request_user_input` to collect the source path and schema before
calling `knowledge_scan`. Keep tool calls observable in the timeline.

## Required workflow

1. Call `knowledge_scan` on the source directory first. Keep the returned
   project id and use only source paths from its manifest. The runtime creates
   a resumable `knowledge/task.json`; do not reset it by starting a second
    scan unless the user asks to rebuild the project.
2. For compound/scanned `.doc`, `.docx`, `.pdf`, or image sources, call
   `knowledge_normalize` in bounded passes before extraction. Start with a
   small OCR asset budget, inspect the manifest, then continue only for useful
   assets. Plain text/Markdown sources do not require this step.
3. Read or inspect the relevant source files, then call `knowledge_extract`
   with typed page drafts (`entity`, `concept`, `source`, `query`,
   `comparison`, or `synthesis`) and source-linked evidence.
   For scanned PDFs, use `read_file` with its bounded `pages` argument before
   extracting; keep the PDF itself as the raw source and do not paste the
   whole document into the runtime context.
   The scan manifest records an `ingestion_adapter` and bounded
   `extraction_mode` for each source. Use that contract to choose line-,
   page-, text-, or vision/OCR-bounded reads; raw bytes remain evidence, not
   an instruction to inject the whole source into context.
   For a compound tender, do not collapse all qualifications or all images
   into one giant page. Read the generated `image-catalog.md` in bounded
   ranges, then create one evidence-grained entity/page for each materially
   distinct certificate, licence, contract, invoice class, company, product,
   requirement, or unresolved image group. Preserve the parent tender as a
   source page and connect the smaller pages with evidence-backed relations.
4. Call `knowledge_compile` only after the selected extraction work is saved.
   By default this creates a candidate ChangeSet under `knowledge/candidates/`
   and writes merged Markdown views there (legacy projects may use
   `knowledge/wiki/`); it does not update the published Wiki.
5. Call `knowledge_validate` with the candidate ChangeSet id and resolve
   missing evidence, conflicts, or wikilinks.
6. Apply the current workspace execution policy to the candidate. In `auto`,
   `knowledge_publish` applies it directly; in `ask`, it creates a durable
   approval form and must be replayed after the user chooses Approve; in
   `read_only`, publishing is blocked. Use `knowledge_reject` to save feedback
   without modifying the published Wiki. Never treat a model tool call alone
   as human approval while the policy is `ask`.

### Extraction quality contract

`knowledge_extract` stores typed IR; it is not a free-form Markdown dump. Each
page draft must contain a substantive `body`, and each entity must contain a
substantive `description`. For `entity`, `concept`, and `source` pages, always
provide semantic `tags` as well as `sources`. Include `related` page titles or
slugs when the source establishes a relationship. A minimal valid shape is:

```json
{
  "pages": [{
    "type": "concept",
    "title": "Agent Runtime",
    "slug": "agent-runtime",
    "body": "A multi-paragraph explanation grounded in the source, including its boundary, responsibilities, and limitations.",
    "tags": ["runtime", "agent"],
    "related": ["LangGraph"],
    "sources": ["doc/runtime.md"]
  }],
  "entities": [{
    "name": "LangGraph",
    "type": "entity",
    "description": "A graph-based orchestration framework that models agent execution as durable state transitions and exposes explicit edges between steps.",
    "tags": ["framework", "orchestration"],
    "related": ["Agent Runtime"],
    "source_path": "doc/runtime.md"
  }]
}
```

Never submit an empty body, a title-only body, or an entity with only a name.
Claims are fact-level assertions with a bounded `evidence` list. Each evidence
item must retain its source path and should include a line/page/image anchor,
quote, extraction method, and confidence when available. Uncertain or
conflicting claims must use an explicit status or review hint rather than be
presented as certain.

`knowledge_validate` is a publish quality gate: it checks substantive body
length, semantic tags, source evidence, relation targets, and graph evidence.
If it reports `quality` issues, re-extract the affected source with richer
content, then run compile and validate again; do not patch generated wiki
Markdown directly. Relation edges are also materialized into page `related`
metadata by the compiler, so an explicit `related` list is useful but not the
only way to preserve relationships.

## Evidence and retrieval rules

- Preserve the source-relative path in every page's `sources` field and in
  relation evidence whenever possible.
- Do not directly edit generated wiki Markdown during extraction. The typed
  Knowledge IR is the durable hand-off between the Agent and compiler.
- Do not inject the entire wiki into an ordinary chat turn. When the user has
  selected a project, use `knowledge_search` with `page_type`, `tag`, or
  `source_path` filters when useful. Quote the bounded `quote`/`citation`
  object and prefer its `source_citations` when writing evidence-based text.
- For a multi-part knowledge question, use `knowledge_research` with two to
  four focused queries and a small budget; inspect its `stop_reason` and
  citations before answering. The graph is supporting context, not proof by
  itself.
- For evidence-dependent writing, pass the returned `citations` to
  `writing_changeset.sources`. If `sources` is omitted, the writing tool may
  carry the latest citations from the currently selected Knowledge project;
  never mix citations from another project.
- If validation reports a `conflict`, do not publish it silently: compare the
  cited source documents, record the resolution in a query/synthesis page or
  revise the extraction, then validate again.
- If a source or page is uncertain, record a query or synthesis page and state
  the uncertainty instead of inventing a relation.
- Large compound office sources must be normalized incrementally. Legacy DOC
  files may contain a small text stream and a very large asset stream; never
  load the latter into memory. Process recovered images independently, retain
  their asset ids/offsets, and keep unprocessed assets resumable.
- OCR image labels (`document_type`, `tags`, `entities`, `sensitive`, and
  `review_status`) are routing hints. Do not publish account numbers, identity
  numbers, invoice values, licence dates, or company attribution solely from
  a heuristic label. Review the bounded OCR/image evidence and create a query
  or confirmation issue when the value is ambiguous.
- If a writing task exposes missing knowledge, use a non-blocking evidence
  request (`allow_message_response=true`, `accepts_attachments=true`). New
  evidence is task-local by default. Only update the durable Knowledge project
  through extract → compile → validate/review → publish after the user chooses
  that scope.
