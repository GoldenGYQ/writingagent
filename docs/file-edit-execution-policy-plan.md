# File edit execution policy and HITL plan

## Goal

Add a file-mutation execution policy that is independent from workspace access:

- `read_only`: file mutations are rejected before the tool writes anything.
- `ask`: mutations are converted into a durable proposed change and require explicit user approval.
- `auto`: preserve the current behavior and apply valid mutations immediately.

Workspace access (`restricted` / `full`) continues to answer **where** tools may operate. The
execution policy answers **whether** a mutation may be applied without human approval.

## Protected invariants

1. The frontend is not the authority. It may submit only an interaction id and an action.
2. A pending proposal stores server-resolved targets, the exact tool arguments, and before hashes.
3. `ask` never mutates the target file before approval.
4. Approval is single-use. Replaying an old approval cannot authorize another mutation.
5. If any target changed after proposal creation, the approval becomes stale and the mutation is
   not applied.
6. Existing sessions and non-WebUI channels retain `auto` behavior unless explicitly configured.
7. The existing post-apply file activity/diff remains the audit record after an approved apply.

## Implementation steps

### 1. Policy model and wire contract

- Extend the effective workspace scope with `execution_policy`.
- Validate the three allowed values at the backend boundary.
- Persist it with the session workspace scope and expose it in WebUI bootstrap/session payloads.
- Treat policy changes as workspace-control changes: localhost only and unavailable while a turn
  is running.

### 2. Server-owned change proposals

- Add a small file-change proposal module owned by the agent/tool boundary.
- Recognize `write_file`, `edit_file`, and `apply_patch` as file mutations.
- Simulate mutations without writing, producing target paths, before hashes, line statistics, and
  bounded unified diffs.
- Store the proposal in the session interaction state with `kind=change_approval`.

### 3. Enforcement and continuation

- Add one narrow policy gate immediately before tool execution.
- `read_only` returns a non-retryable policy error.
- `ask` creates a proposal and pauses the run on first call.
- After approval, only an exact replay of the same tool name and arguments may consume the
  single-use grant and execute.
- Rejection returns a structured denial; stale hashes require a fresh proposal.

The first implementation resumes through the existing durable interaction continuation. The model
replays the approved tool call; the backend, not the model, decides whether that replay matches the
approved proposal.

### 4. WebUI

- Add a keyboard-accessible execution-policy menu next to workspace access.
- Add a dedicated change-approval card showing files, risk, additions/deletions, and expandable
  unified diff.
- Provide explicit `Apply once` and `Reject` actions. Closing or ignoring the card is not approval.

### 5. Verification

- Backend tests: payload validation, read-only blocking, no pre-approval writes, exact approval,
  rejection, replay prevention, and stale-file detection.
- WebSocket tests: durable approval request and response validation.
- Frontend tests: policy selection payload, accessible labels, diff rendering, and action payloads.
- Run focused pytest, WebUI tests, TypeScript build, and Ruff on changed Python files.

## Deliberate first-version boundary

Shell commands can mutate files in ways that cannot be reliably classified from command text. This
version therefore makes the file-tool policy authoritative for nanobot's structured file tools. A
follow-up hardening step should either require approval for every shell command under `ask`, disable
shell under `read_only`, or run shell commands in a staged OS-level sandbox/worktree and approve the
resulting ChangeSet. The UI must not claim shell-level write isolation until one of those mechanisms
is enabled.
