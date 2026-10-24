# Native STE chat and TLS compatibility

Authorized by the user on 24 October 2026: implement the proposed native Qt/QML chat in STE Slicer, preserve the web version, and attempt to fix a teammate's certificate-verification error.

## Intent and constraints

- Add **Открыть чат в STE Slicer** alongside the existing browser action.
- Reuse one local server, agent, settings and saved conversations between both interfaces.
- Keep all existing HTML, CSS and JavaScript byte-for-byte unchanged.
- Support STE's Python 3.5.7, Qt/PyQt 5.10, plugin API 5 and existing HTTP API 3.
- The native window is nonmodal and does not prevent work on the print scene.
- Keep conversation, scene/files and developer events in distinct views.
- Provide conversation history, attachments, settings, requirements, send/cancel/continue, manual tools and result saving.
- Preserve drafts when operations fail and when the window is hidden. Background polling cannot overwrite edits.
- HTTP and file work run outside the GUI thread; stale worker responses cannot change a newer or stopped session.

## Approach

Use a direct QML `Window`, a QObject controller and an authenticated loopback client for the existing HTTP API. This reuses validated backend operations and leaves the web interface unchanged. A browser embedded in Qt would require an absent QtWebEngine component; duplicating the agent/backend would split history and state.

Supplement the system trust roots with an exact vendored certifi 2026.10.19 CA bundle. Keep certificate and hostname verification enabled, preserve the actual completion transport's explicit SSL context, and provide actionable certificate errors without retrying the request. Record the bundle's source, hash and license. The teammate's exact cause remains unconfirmed until checked on his computer.

## Verification

Use synthetic conversations and local test servers; do not use a real API key or call a model. Test real TLS handshakes, actual STE Python/QML, GUI lifecycle and asynchronous races. An unauthenticated public GET may check provider TLS; an HTTP response confirms transport only. Compare web hashes, inspect native screenshots and inspect the packaged plugin resources.

Screenshots belong in `docs/screenshots/`; transient logs, manifests and fixtures belong in ignored `.runtime/`. Do not place test debris in organizer result folders.
