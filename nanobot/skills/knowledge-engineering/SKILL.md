---
name: knowledge-engineering
description: "Build and maintain a workspace-scoped structured knowledge wiki from source documents. Use knowledge_scan, knowledge_extract, knowledge_compile, knowledge_validate, knowledge_publish, and knowledge_search; preserve source evidence and merge existing pages."
metadata: {"nanobot":{"emoji":"📚"}}
---

# Knowledge Engineering

Use this skill when the user asks to build, update, inspect, or cite a project
knowledge base. `/knowledge <source-directory>` is an entry point into the
normal Agent Runtime; it is not a hidden workflow. Keep tool calls observable
in the timeline.

## Required workflow

1. Call `knowledge_scan` on the source directory first. Keep the returned
   project id and use only source paths from its manifest. The runtime creates
   a resumable `knowledge/task.json`; do not reset it by starting a second
   scan unless the user asks to rebuild the project.
2. Read or inspect the relevant source files, then call `knowledge_extract`
   with typed page drafts (`entity`, `concept`, `source`, `query`,
   `comparison`, or `synthesis`) and source-linked evidence.
3. Call `knowledge_compile` only after the selected extraction work is saved.
   Compilation writes merged Markdown views under `wiki/` (legacy projects may
   use `knowledge/wiki/`); it does
   not overwrite an existing page body.
4. Call `knowledge_validate` and resolve missing evidence or wikilinks.
5. Call `knowledge_publish` only after validation passes.

## Evidence and retrieval rules

- Preserve the source-relative path in every page's `sources` field and in
  relation evidence whenever possible.
- Do not directly edit generated wiki Markdown during extraction. The typed
  Knowledge IR is the durable hand-off between the Agent and compiler.
- Do not inject the entire wiki into an ordinary chat turn. When the user has
  selected a project, use `knowledge_search` with `page_type`, `tag`, or
  `source_path` filters when useful. Quote the bounded `quote`/`citation`
  object and prefer its `source_citations` when writing evidence-based text.
- For evidence-dependent writing, pass the returned `citations` to
  `writing_changeset.sources`. If `sources` is omitted, the writing tool may
  carry the latest citations from the currently selected Knowledge project;
  never mix citations from another project.
- If a source or page is uncertain, record a query or synthesis page and state
  the uncertainty instead of inventing a relation.
