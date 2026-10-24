# STE Assistant 0.3 implementation plan

Canonical version 2 plus root amendments A/B, approved by distinct Planner and Advisor on 2026-10-24.

1. Preserve baseline dirty files and installed source. No resets, commits, provider calls, scene changes or automatic restart. Use Python 3.5 stdlib and vanilla browser assets.
2. Native executor owns slicer.py and native tests: support actual Processing start, optional existing slicingStarted, explicit matching Done, deferred finalization after native postprocessing, fresh nonempty G-code and existing plate/generation/fingerprint safeguards. Test native sequence, negatives and settings bounds.
3. History executor owns core.py/server.py/conversations.py/AIAssistant.py/dev.py and new history tests: optional history_dir; atomic bounded store; exact redaction; metadata-only API; operation locking; safe restart checkpoints and outputs; empty-store-only public migration. Existing APIs and run/document behavior remain.
4. Frontend executor owns web assets: top tabs, local conversation sidebar, composer conditions dialog/chips, scene dropdown, full Developers journal/steps, safe Markdown and api_version compatibility notice. Remove the requested brand/left-form elements; desktop only.
5. Root integrates, reviews and verifies native and history/API tests under bundled and actual host Python; browser fixtures and all eleven comments at 1159 x 668 and desktop widths 1024–1920; XSS and explicit G-code export click. No external model requests.
6. Root captures public legacy chat, migrates only if the target store is empty, updates docs/version, creates a checked ZIP, and installs verified source with backup and before/after hashes. Do not restart STE. Report the controlled restart needed to activate Python and the limits of live verification.

Root amendment A: Processing OR an actual optional slicingStarted is valid current-generation start; Done with no start remains invalid. Do not add a start-scene guard that mistakes STE-generated basement nodes for user edits.

Root amendment B: source installation is authorized and will be completed; only the user-controlled application restart remains pending. The revised Planner's wording that left installation pending is superseded.

User follow-up: finish the work and upload changes to GitHub. This explicitly authorizes the final task commit and push after verification; the earlier no-Git-mutation constraint is superseded for these actions. Preserve unrelated files and exclude local runtime, credentials, conversations and generated test artifacts.

Finding ledger F1–F11: generation-scoped completion/deferred snapshots; present-only bounds; pre-decode and pre-replace byte limits; exact recursive secret fields; mutation lock/HTTP400; metadata-only restoration and interrupted recovery; canonical file containment; truthful empty-only public migration; DOM Markdown/XSS; API capability boundary and backed-up install; host/native/history/browser regression coverage. All incorporated in the design and executor packets.
