# Native STE Chat and TLS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track checkable task results below.

**Goal:** Add a native chat window while preserving the web UI and strengthen strict TLS trust compatibility.

**Architecture:** Reuse the current AssistantServer through a fixed authenticated loopback client. A QObject controller performs background I/O and feeds a direct nonmodal QML Window. Supplement platform TLS roots with a pinned CA bundle.

**Tech Stack:** Python 3.5.7, PyQt/Qt 5.10, QtQuick.Controls 1.3, QtQuick.Window 2.1, plugin API 5, HTTP API 3.

**Spec:** `docs/superpowers/specs/2026-10-24-native-chat-tls-design.md`

Canonical plan v3; independent Advisor approved v3. Findings A1-A10 incorporated: correct paths; direct Window; generation and operation ordering; pinned additive trust; any HTTP TLS smoke result; verified Qt capabilities; submission-safe evidence; existing API contract; package inspection; actual completion HTTPS context wiring/test.

## Global Constraints

- Preserve existing web HTML/CSS/JS bytes and the browser menu action.
- No paid/model calls, automatic provider retries, insecure TLS or global trust changes.
- One shared server, native controller and native window. All I/O outside the GUI thread.
- API keys and session tokens never enter public state, logs, screenshots or packaged evidence.
- Store temporary evidence in `.runtime/`, screenshots in `docs/screenshots/`.

## Review Focus

- Invalid TLS must reject the peer before credentials are sent; completion must use the augmented context.
- Old idle polls cannot re-enable submit while a newer command is pending.
- Hide/stop/reopen must reject stale worker completions without losing accepted submissions.
- Polling and failed operations must preserve editable drafts and attachments.
- Packaged QML/CA resources must resolve after installation; web hashes must remain identical.

## Tasks

- [x] Root baseline: clean base `5a6e50e`, branch `codex/native-chat-tls`, inventory and web hashes recorded. Native capabilities Qt/PyQt 5.10 verified. Existing suite: 132 tests, OK, one symlink skip. Strict unauthenticated provider TLS GET: HTTP 401.
- [x] TLS executor: modify `plugin/AIAssistant/agent/core.py`, add `resources/cacert.pem`, `NOTICE`, `LICENSE`; extend `tests/test_tls.py` and `tools/host_tls_smoke.py`. First reproduce missing behavior, then verify trusted/untrusted/wrong-host handshakes, retained system roots, isolated bundle trust, bundle failures, redacted certificate diagnostics and no retry. Preserve and test `completion()` passing `provider_ssl_context()` through `HTTPSHandler` on its actual request path.
- [x] Native executor: add `agent/native_client.py`, `native.py`, `qml/AssistantWindow.qml` and native tests. Fixed API-3 routes; no proxies/redirects; authenticated atomic downloads. Controller uses generations, operation IDs, one-poll limit and command fence. GUI file picker and background upload. Direct nonmodal Window; composer-only Enter/Shift+Enter; separate chat/scene/developer views; settings/requirements/history/attachments/manual tools.
- [x] Root integration: extract `_ensureServer()` in `plugin/AIAssistant/AIAssistant.py`; preserve `showChat()`, add native menu and lazy reusable controller/view; asynchronous stop. Update `plugin/AIAssistant/plugin.json` to 0.4.0 and host smoke to three menu actions.
- [x] Root verification: full suite with external network blocked; actual STE QML loading and lifecycle; stale-response and draft tests; isolated synthetic screenshots; strict credentialless provider TLS smoke. Record raw results in `.runtime/`.
- [x] Root delivery: update README/plugin README/API contract/verification; package and inspect required native/QML/CA/license resources; compare original web hashes; back up and install verified plugin; report precise verification limits.

## Executor ownership

TLS owns only core TLS implementation, CA resources, TLS tests and TLS smoke. Native owns only new native modules, QML and native tests. Root owns extension integration, shared documentation, release metadata, installation, packaging and final review. Do not commit, push or change existing web assets during executor work.
