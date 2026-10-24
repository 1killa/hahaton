# Chat documents and resumable runs implementation plan

> **For agentic workers:** Follow the bounded task packet supplied by the root. No descendants. Track checks below.

**Goal:** Support DOCX/TXT specifications, resumable long requests, and a readable chat without technical logs.

**Architecture:** Existing local HTTP plugin and vanilla SPA. Store complete provider/tool exchanges in session history and pause before the next provider call. Parse document text locally with stdlib.

**Tech Stack:** Python 3.5 stdlib, STE/PyQt5 host, HTML/CSS/JavaScript.

**Spec:** `docs/superpowers/specs/2026-10-24-chat-documents-design.md`.

## Global constraints

- Python 3.5 compatible; no new dependencies.
- Preserve loopback Host/Origin/token checks, secret redaction and no automatic provider retries.
- 48 provider steps / 600 seconds per explicitly initiated run; checkpoint only complete tool batches.
- DOCX/TXT are document data, never model imports. Maximum 120000 extracted characters, no silent truncation.
- Preserve original supplied documents and user configuration. No real slicing/export claims from DEMO.
- Work in the existing clean checkout; no commits, push, remote deployment or Git state changes.

## Review focus

- Cancellation inside a multi-tool batch must retain valid tool pairs without executing remaining mutations.
- Continue, new-message and manual-tool operations share the same exclusive operation lock.
- No duplicate user message or replay of completed tools on continue.
- File upload/send races cannot drop files or drafts; log updates cannot move chat scrolling.
- DOCX XML/ZIP and TXT decoding are bounded and reject malformed/binary/empty inputs explicitly.

### Task 1: Document ingestion

**Files:** create `plugin/AIAssistant/agent/documents.py`, `tests/test_documents.py`; modify `agent/server.py`, `tests/test_server.py`.
**Interfaces:** `extract_document(filename, data) -> str`; `validate_upload` keeps `(extension, kind, mime)`; document registry/resolve includes `text`, `text_chars`; public upload returns `id,name,kind,mime_type,text_chars` without paths/body.

- [x] Add failing parser/HTTP tests: Unicode encodings, DOCX paragraphs/tables, invalid archives/XML/limits, document ID resolution.
- [x] Implement bounded stdlib extractor and upload integration, no `.doc` false support.
- [x] Run focused parser/server tests and report exact results.

### Task 2: Resumable agent and document context (root)

**Files:** `agent/core.py`, continuation route in `agent/server.py`, `tests/test_agent_runs.py`.
**Interfaces:** `AgentSession.continue_pending()`; `POST /api/continue {}` returns 202 or existing ToolError 400; state gains `status,can_continue,pause_reason,model_steps`.

- [x] Add failing tests: 13+ rounds finish, 48-step pause, same-turn continuation/no replay, time budget, cancel/API failure/history, document content not model import.
- [x] Persist whole turns at start, fill skipped tool results on cancel, retain completed context on errors; checkpoint continues without new user message.
- [x] New message after pause archives prior checkpoint and begins a fresh turn; cancel/reset clear continuation.
- [x] Serialize documents into separate marked untrusted text content blocks, using attachment `text`.
- [x] Add authenticated continuation route after Task 1 releases server.py; run focused tests.

### Task 3: Chat interface

**Files:** `web/index.html`, `web/app.js`, `web/style.css`.
**Interfaces:** document chips consume `kind,text_chars`; Continue consumes top-level `can_continue,pause_reason,model_steps,status` and `POST /api/continue {}`.

- [x] Move `#event-log` outside `#chat-body` into closed right-panel details with independent scrolling; only messages affect chat scroll.
- [x] Improve typography/column proportions/Russian labels and responsive layouts; preserve all existing actions/settings/pins/results.
- [x] Add Continue status card, DOCX/TXT picker and typed removable attachment chips, upload/send in-flight guards and upload status.
- [x] Keep textarea editable while busy; preserve edits made while send request is pending.
- [x] Run JS syntax and browser QA after backend integration.

### Task 4: Integration and delivery (root)

- [x] Independently review all diffs and run full suite with workspace-local TEMP and loopback permissions.
- [x] Run suite and host smoke via `tools/run_host_python.ps1` on STE Python 3.5.
- [x] Verify supplied gear DOCX upload through local HTTP into captured provider context; no external API calls.
- [x] Browser verify desktop window sizes, collapsed logs, log-only scrolling, attachments/errors, draft and Continue.
- [x] Update README/VERIFICATION and workspace checkpoint; package draft plugin archive.
- [x] Back up installed AIAssistant, copy verified sources without changing preferences, compare hashes. Explain STE restart requirement.

## Ledger

- Baseline: clean main checkout. 34 tests: 33 passed, 1 skipped; sandbox loopback initially denied, same suite outside sandbox passed.
- Ruling: task already authorizes reversible code/UI changes; no extra design approval prompt.
- Ruling: allow a new message at pause with preserved completed context, instead of forcing reset/continue; supports changing the request.
- Planner: PLAN_REVISION v3; Advisor: PLAN_APPROVED. F1-F9 incorporated; F10 clarified: a step counts one provider HTTP request, never local tool calls. Deadline is cooperative between requests/batches.
- Root: 12 regression tests passed after reproducing failures; current turn/history, cancellation pairing, continuation ownership and document context implemented. Server route waits for document executor to release server.py.
- Document and frontend executors dispatched with disjoint ownership. Root retains integration and final verification.
- All tasks complete. Final suite: 63 tests, 62 passed / 1 skipped on bundled Python and actual STE Python 3.5.7; host smoke PASS. Browser behavioral QA and polling-race regression PASS; 7 widths without horizontal overflow. Review findings resolved.
- Installed 0.2.0: 15 source files SHA-256 verified against installed copies; original plugin backed up under `.runtime/backups/AIAssistant-pre-0.2-2026-10-24-da0cb3f3`. Preferences unchanged. User restart activates the updated Python code.
- User scope correction: browser on PC only. Removed phone-specific breakpoints and mobile viewport rules; retained the desktop narrow-window layout. Phone optimization is outside scope.
