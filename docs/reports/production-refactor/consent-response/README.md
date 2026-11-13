# Consent response verification

Approving or denying one tool previously returned `grant_active=false` even when
an existing read-only grant remained active. The UI uses that response to update
its approval picker, so the displayed selection disagreed with the next tool's
actual authorization. Console and Studio decision responses now report the
existing context-bound grant; the Console file approval response does the same.
This does not grant permission for a write or change the chosen read-only policy.

Five of ten new cases reproduce the incorrect response on the parent. All ten
pass after the correction, including active/inactive grants, Allow/Deny on both
surfaces, and file approval. The 36-case focused set also checks ownership and
secure approval. Regression assertions verify the broker decision and that
write permission remains absent from the read-only grant.

Four attempts through the running API are retained. The first two scripted
provider fixtures omitted an explicit JSON-schema capability, so their write
flows did not reach the intended tool. The third reached both decisions but had
an incorrect assertion that denial must end the conversation with `denied`.
The shared loop permits an explanatory answer after its first denied tool. The
corrected fixture instead requires exactly the denied tool status, with no
running/done status, and accepts the documented turn completion behavior.

All four final flows pass: Allow and Deny preserve the read-only grant, and the
following reads run without another approval. The synthetic provider invokes
only `SELECT 6 * 7 AS total`; the write-classified tool is used to exercise its
mandatory consent without altering data. Every attempt removes its two owned
threads, grants, provider/model and login. No existing model/default configuration
changes. The provider runs locally and is controlled test evidence, not live AI
acceptance; the earlier real-provider failures remain open.

The SQL worksheet documentation explains the distinction between one action's
Allow/Deny buttons and the read-only picker. Its browser tests pass and the new
paragraph was viewed at `/docs/sql/sql-workspace`. No SQL/Python example changes.
Exact source hashes, suite counts and commands are in commands.json. The 875-call
inventory is rechecked; the two Agent router SQL sites have unchanged function
AST and call hashes. Earlier live schema timestamps remain unchanged.

Durable PostgreSQL consent, exact pending-action/run/step identity, mid-turn
revocation, domain migration, runtime separation and release acceptance remain
open. This correction does not promote any whole-feature acceptance row to PASS.
