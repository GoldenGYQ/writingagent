---
name: knowledge-rag
description: Use the published Knowledge Wiki and graph through knowledge_search instead of injecting the whole knowledge base.
---

# Knowledge RAG

The primary interface is the `knowledge_search` tool during normal conversation. Use it when a question concerns facts, concepts, entities, sources, or relationships already present in the selected Knowledge project. The Wiki and graph are optional navigation/inspection surfaces, not prerequisites for retrieval.

## Retrieval policy

- Prefer `mode="hybrid"`; use `mode="vector"` for semantic-ish topical lookup and `mode="graph"` when the question is about relationships or architecture.
- Keep `limit` small and use `expand_hops=1` by default. Increase to `2` only when the user asks for surrounding context.
- Apply `page_type`, `tag`, or `source_path` filters when the question gives a scope.
- Treat `documents`, `relations`, and `claims` as bounded evidence, not as permission to invent facts.
- Cite returned `citations`/`source_citations` in the answer. Graph relations help discover context but are not the sole proof of a claim.
- If retrieval returns no result or a low-confidence/unclear result, say so and ask for a narrower query or source; do not silently substitute memory.

## Knowledge gaps during writing

When a required fact is absent, do not repeatedly ask an unstructured question.
Use `request_user_input` with `reason="knowledge_gap"`,
`allow_message_response=true`, and `accepts_attachments=true`. Explain:

- use `response_scope=task` by default;
- use `response_scope=knowledge_candidate` only when the user explicitly wants the material retained; it must still pass Knowledge review before publication;

- the missing fact or evidence;
- why it is needed and which document section depends on it;
- acceptable evidence (for example a certificate, invoice, table, photo, or a short answer);
- whether the response should be used only for the current draft or proposed
  to the long-term Knowledge project through extract/review/publish.

The user may then reply naturally or drag files/images into the composer. Treat
OCR as candidate transcription, preserve attachment paths, and ask for
confirmation when a low-confidence value would affect compliance, price,
qualification, dates, or legal identity.

For a multi-part question, use `knowledge_research` with two to four focused `queries` and a small `budget` (normally 2–3). Let the tool merge duplicate documents and evidence, then inspect `retrieval.stop_reason` before answering. Use one `knowledge_search` call for a simple fact; do not invoke research repeatedly without a new sub-question.

The tool uses a local derived index and never requires the complete Wiki to be placed in the model context. Published pages remain governed by the existing extract → compile → validate/review → publish flow.

Future Agentic RAG orchestration should remain above the runtime kernel: decompose a complex question, run a small number of bounded searches, merge and de-duplicate evidence, then stop when the evidence is sufficient or the retrieval budget is exhausted. Do not turn this Skill into an unconditional context injection.
