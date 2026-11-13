# Stage registration boundary

R36 was reproduced on the existing local patched-engine/Ranger stack with a
disposable user, role, two databases, two registrations and one synthetic file.
The user had SELECT/INSERT/DELETE only in its own database. A direct download
from the other scope returned 403, but registering the other stage's prefix in
the authorized scope returned 201 and exposed the same canary through a 200
download. The original failed acceptance cases remain in the evidence packet.

## Current correction

New ordinary registrations allocate `nova/stages/<server-generated UUID>`.
The caller cannot choose that location through a stage name, database name or
an object ID. The existing schema DELETE authorization still runs under the
authenticated principal and exactly one active role. A nonempty custom prefix
additionally requires active ACCOUNTADMIN, using the existing active-role gate.
An assigned but inactive administrator role does not satisfy this check.
Administrative custom prefixes must resolve to a relative folder, not a root,
parent traversal or backslash path. No storage credential is resolved to create
the metadata row.

Creation and both file/SQL execution require an exact configured connection
name. A missing name no longer borrows the workspace default connection.
SQL continues to authorize all selected stage scopes before resolving secrets.
Registration fields enforce the source table's 128-byte name/scope/connection
widths and 512-byte prefix width, including multibyte input. Refused registration
authorization and its outcome are included in the existing redacted audit path.

The Stage Manager offers custom prefixes only to the active administrator.
Changing identity, active role or security-context version discards the draft
and selected stage. Stage and file query caches include the security epoch.
The connection field must be filled before submission. Existing API routes,
response fields, registration IDs, owners and file locations remain unchanged.

The multipart upload route now reads the current folder sent by Stage Manager.
It validates both folder and filename before storage, joins them as a relative
path and audits that complete path. Previously the route ignored the folder
field and silently placed the file at the stage root. Invalid absolute, parent
traversal and backslash folders are rejected before any storage operation.

Creating a new registration with a previously deleted name now allocates a new
folder. It does not reconnect the old files. An administrator can explicitly
register an existing folder. Deleting a registration continues to leave its
files in storage. The user-facing instructions are in
[Stages](../../../frontend/src/features/docs/content/data/02-stages.md).

## Verification and limits

The local after-fix API probes prove the same cross-scope registration is
refused, ordinary creation/upload/download/delete still work, and unknown
connections and oversized input fail before persistence. The docs LIST,
SELECT and COPY examples ran with a synthetic CSV and an isolated table; the
loaded rows matched `[[1, 10], [2, 20]]`. A separate browser check created an
empty stage, inspected the form at 390 × 844 and verified keyboard focus.
The updated docs rendered at `/docs/data/stages`. A separate upload check
reproduced the ignored-folder defect before the fix, then passed all seven
live folder and invalid-path cases. Normal browser upload placed the selected
CSV in `incoming`; both fixture files were content-verified and removed before
deleting the empty registration.

One broader live attempt timed out while local unit and browser coverage ran
together. Its cleanup could not delete a canary and did not reach the uploaded
CSV. Both exact owned locations were recovered from the fixture prefix and
deletion audit, registered temporarily by the administrator, content-verified,
emptied and deregistered. The timeout and failed cleanup are retained alongside
the successful recovery; they are not counted as a passing attempt.

The [evidence packet](../../reports/production-refactor/stage-registration/README.md)
records attempts, checks, source hashes and remaining gates. This change does
not switch Stage metadata authority. The subsequent
[prepared PG domain](PG_STAGE_REGISTRATIONS.md) verifies logical name uniqueness,
revision CAS, schema-scoped access and transactional audit. Runtime caller
wiring, historical registration provenance and final cutover remain open. Existing rows are preserved, not retrospectively
certified as safe imports. The current local registrations were not rewritten.

All 16 metadata callsites affected by changed source-file hashes were reviewed
again. Fifteen retain the same call AST; the Stage INSERT changes only its
prefix parameter. All still address their original metadata tables. The
inventory retains 875 classified calls and 759 feature rows.
