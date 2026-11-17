# Public export audit scope

Updated 2026-11-17 for portable changes through source revision `6547149`. This is a local-only preparation; independent clean-clone validation remains pending.

## Included and excluded

Included: portable Python entrypoints, the result contract, core/development/optional semantic requirements, fictional tests and offline evaluation/import checks. Model failures never become saved answers; failed DOCX updates preserve the previous note.

Excluded: official PDFs, real indexes, private notes/raw records, credentials, runtime data, private DOCX templates, source Git history/rules, sibling-bot retrieval and its seven failure cases, and host/friend/SAP-tenant/mail integrations. Notebook IDs remain empty, capture stays opt-in and notes use a user-owned application directory. The public local-index retrieval regression is retained. The source's two removed one-off font scripts were never exported.

## Sequential audit gates

1. Credentials: inspect common strong-secret formats, literal credential assignments and environment examples. Only empty keys, code identifiers, detection patterns and synthetic test values are allowed.
2. Identity: inspect the preparation denylist, email/phone/account/UUID patterns and natural-language fixtures. Only fictional examples are allowed.
3. Paths: fixed POSIX/Windows host paths are prohibited. Build paths from the environment, user home or the project directory. Python entrypoints are invoked with the interpreter.
4. Hygiene: candidate content must be text, with no symlinks. Ignore environment/configuration secrets, runtime, data, logs, indexes, databases and key files. Git metadata and ignored interpreter caches/dependencies are outside the export payload.
5. Tests: run all public tests, the fictional retrieval gate, import smoke and entrypoint syntax checks in the repository. Any failed round stops the sequence and prevents a commit. Actual provider calls, adapter delivery, semantic downloads and independent clean-clone acceptance are separate.

The dated sequential results, hit counts and test output are retained in the local execution report outside this public repository. An audit pass does not establish real provider, tenant or document-corpus acceptance. No real secret or identity values are reproduced in this document.
