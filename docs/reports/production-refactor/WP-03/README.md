# WP-03: schema translation and import values

Status: **IN_PROGRESS**. The source-column mapping, strict conversion helper and
duplicate-preserving identity contract are implemented. Complete target DDL,
logical uniqueness/FKs, per-domain access, timezone provenance and validated
export/import orchestration remain open. No domain is serving PG metadata.

Source checkpoint: parent `a7894247db84c9a9627b5c9a390a4c6636ef0d9a`, application
tree `e16247c8315832918e30500757d50fd38d3da28885a5b93e7c1c0ff7a5e4d38c`.
The installed schema package remains revision `0001_deployment_state.sql`;
there is no new domain DDL or published metadata epoch in this checkpoint.

[Wave L](../../../architecture/production-refactor/WAVE_L_REVIEW.md) explains
the seven duplicate-key mappings and the historical dashboard DDL evidence.
[COLUMN_TRANSFORMS.csv](../../../architecture/production-refactor/COLUMN_TRANSFORMS.csv)
covers all 1,578 observed columns across 157 live tables, linked to recorded DDL
digests. All 249 DATETIME columns retain an explicit unresolved-provenance status.
This covers value types, not complete relational/domain semantics.

`core.metadata_import` performs no I/O and cannot select a metadata authority.
Its binding conversion refuses coercion, truncation, duplicate JSON keys,
invalid Unicode, unknown types and ambiguous timestamps. JSON numbers pass to
PG as validated original JSON text, preserving precision beyond binary doubles.
SQL NULL and JSON null remain distinct. Errors omit source payloads.
An explicit proven source zone is required to convert a naive timestamp;
current engine timezone is not used as a blanket assumption.

`import_row_id` assigns each occurrence in an immutable export a deterministic
UUID from snapshot/table/chunk-digest/row-position. It retains identical rows and
does not replace legacy object IDs. It only validates address syntax: the future
orchestrator must authenticate/verify the export and freeze checkpoint before
importing. A complete import or cutover is not implied by the helper tests.

## Local verification

The tests reuse the existing `docker-nova-metadata-db-1` PG17.11 TLS fixture in
the `docker` project. No container was created or removed, and no StarRocks,
Ranger, Redis or object-store data was reset. The final combined suite passed
61 cases without skips in 14.84 seconds; its sanitized report is
[metadata-import-local.xml](metadata-import-local.xml).

The two additional PG cases bind converted legacy values through the real
psycopg transaction path and verify exact JSON numbers, large integers, Unicode,
booleans, doubles, UTC instants and null distinctions. Duplicate legacy IDs
survive as distinct row occurrences. Replaying one occurrence conflicts, and
an invalid timestamp rolls back the earlier writes in its transaction.

The initial 85 focused converter cases passed; a subsequent inventory-completeness
case is included in the final backend suite. Full backend coverage passed 10,601
tests with four warnings in 457.59 seconds (65% combined coverage). Mypy, Ruff on
53 changed Python files and the 54-scenario/189-check scorecard passed.
[commands.json](commands.json) records the exact invocations and results.
CI for this source is pending; earlier provisioning/Studio runs do not certify
these additions. No frontend code or executable user-doc example changed.

No production recovery action changed. Existing StarRocks remains authoritative;
do not run an import or publish an epoch using these helpers alone. This work
addresses parts of DB-11/MIG-01/MIG-02 and the redacted-import boundary. It does
not satisfy complete schema translation, two rehearsals or release acceptance.


## Preference storage checkpoint

Revision 0002 now installs the first domain table and its forced RLS/column
grants. The [preference contract](../../../architecture/production-refactor/PG_PREFERENCES.md)
describes ownership, retained deletion revisions, per-workload access, credential
validation and the still-pending caller/cutover work. The earlier revision-1
statement above applies to the import-helper checkpoint, not this schema package.

Final local checks: 10,622 backend tests passed with four warnings in 561.75
seconds (65% combined coverage); mypy, Ruff on 58 changed Python files and the
54-scenario/189-check scorecard passed. The final PG17/PgBouncer suite passed
96 tests without skips in 67.65 seconds, including 27 preference cases.
The repository receives the service transaction and uses a savepoint per batch;
service rollback and a caught batch conflict both preserve atomicity. Fixture
cleanup left no test schemas or new roles. No development data was reset.

[preferences-commands.json](preferences-commands.json) records source/schema
hashes, exact commands, earlier attempts and scope. The sanitized JUnit report
is [metadata-preferences-local.xml](metadata-preferences-local.xml). CI for
these edits is pending; the parent run's Ubuntu mirror DNS failures remain
failed infrastructure evidence and do not certify this source.

## SQL password-flag workload correction

The SQL frontend's managed password-flag command runs in query and proxy.
Two new regression cases failed against revision 0002: query lacked its required
administrator write grant, while proxy could clear its own flag without an
administrator role. Revision 0003 fixes these policies, keeps News and personal
preference writes restricted to web, and preserves prior rows/revisions on
upgrade. No immutable migration was rewritten.

The current local PG run passes 99 tests without skips in 92.62 seconds. The
original two failures and intermediate 98-test pass remain recorded in
[password-flags-commands.json](password-flags-commands.json). The final sanitized
report is [metadata-preferences-sql-flags.xml](metadata-preferences-sql-flags.xml).
The fixture has no remaining test schema or newly created role. Full backend
coverage passes 10,622 tests with four warnings in 1,416.68 seconds (65% coverage).
Mypy, changed-file Ruff (58 Python files), and the 54-scenario/189-check agent
scorecard pass. The application source is unchanged from the preference checkpoint.

CI dependency setup now uses a digest-pinned Playwright image matching the
locked package and a digest-pinned Python test image with libevent/OpenSSL build
dependencies. The latter compiles the checksum-pinned PgBouncer and runs tests
as a non-root user against the disposable TLS database. It mounts only the
private fixture and report directory, without a Docker socket. Chromium must
launch successfully, and all existing lint/build/coverage and PG test guards
remain required. These changes remove the affected per-job Ubuntu apt download;
they do not turn previous setup failures into passes. CI evidence remains pending.

Image contents were checked against the official
[Playwright Docker documentation](https://playwright.dev/docs/docker) and
[Python base-image build dependencies](https://github.com/docker-library/buildpack-deps/blob/master/debian/bookworm/Dockerfile).
Registry manifests supplied the exact digests in the Dockerfile/workflow.
This is CI infrastructure, not the pending production Compose deployment.

The pinned test image subsequently built successfully. Its corrected local
Linux run passes 99 cases without skips; the first loopback-route failure is
retained in the command record. CI 43049859127 passes the 99-case PG job and
frontend job for `1d79c40a`. The run completed with all eight jobs successful;
its engine report has 217 passes, 24 skips and no failures/errors. The skipped
cases remain unproven and do not certify the newer registry code.

## Provider/model storage and separate credentials

Revision 0004 implements provider/model CAS and tombstones, immutable model
descriptor fields, database-enforced alias uniqueness, parent/child locking and
atomic provider/credential revisions. Credentials have their own table and RLS;
ordinary metadata readers cannot select ciphertext. The new ciphertext capacity
preserves the complete encrypted value of an accepted 512-character Unicode key.
The qualified import-type mapping records this reviewed column expansion while
retaining the original source length checks for imported rows.

The final combined PG run passes 132 cases without skips in 119.63 seconds,
including 33 registry cases. The focused import/registry unit selection passes
128 cases. Mypy, Ruff on 61 changed Python files and the 54-scenario/189-check
scorecard pass. Full backend coverage passes 10,664 tests with four warnings
in 620.57 seconds (65% coverage). Fixture cleanup
leaves zero test schemas/new roles; a 90-minute scan of 2,509 fixture log lines
finds zero registry secret canaries or encrypted-envelope markers. No raw logs
were exported.

[ai-registry-commands.json](ai-registry-commands.json) records the source/schema
hashes, earlier failure, corrected expected conflict type, interrupted obsolete
unit run, source aggregates and remaining gates. The sanitized final report is
[ai-registry-pg-final.xml](ai-registry-pg-final.xml). The
[storage contract](../../../architecture/production-refactor/PG_AI_REGISTRY.md)
describes the required caller, alias/job, audit, import and cutover work. There
were no live provider requests in these storage tests; embedding-provider QA
remains owner-waived, and R32 is still open in the legacy runtime.

## Function-alias storage and inventory review bindings

Revision 0007 preserves alias identities, prompts and raw parameters, and adds
CAS, default uniqueness, atomic default moves and retained deletion revisions.
Parent reference guards reject provider/model deletion while an alias still
uses them. The [storage contract](../../../architecture/production-refactor/PG_MODEL_ALIASES.md)
lists the caller, immutable-definition, native UDF receipt, audit and cutover
work still required. No live alias service has switched authority.

The final combined PG17/PgBouncer suite passes 219 tests without skips in
112.41 seconds, including 40 alias cases. The first attempt passed two cases
and failed one because a refused writer received a reference-constraint error
before the permission check. The guard now checks actor permission first.
All four attempt reports remain in
[model-alias-commands.json](model-alias-commands.json), including the earlier
219-case pass before the final line-wrap edit. Mypy, Ruff on 70 Python files and
all 54 agent scenarios / 189 checks pass. The fixture leaves no test schema or
roles. A 25-minute scan of 1,089 PG log lines finds no credential canaries or
encrypted-envelope markers; raw logs are not exported.

The inventory refresher previously keyed reviews only by source-file and AST
hashes, which collide for identical SQL calls at different locations. It now
includes the full source binding and start/end positions. Older line-only CSVs
upgrade only when their binding is unique; ambiguous reviews stop the refresh
before any file is overwritten. Twelve focused tests pass. Running the real CLI
preserves all 875 reviews, all 759 feature rows byte-for-byte, and the previous
live/API observations. The refreshed inventory covers 690 application files.
Inventory preservation does not change an untested feature into a pass.

Full backend coverage passes 10,730 tests with four warnings in 453.58 seconds
(65% combined coverage). CI for these edits is pending. The preceding
job/scheduler commit has passed seven CI jobs, including 179 PG cases without
skips; its real-engine job is still running. No frontend code or executable
user-doc example changed in this checkpoint.

CI 43065274014 subsequently completed for `6714dc5e` with all eight jobs
successful: 179 PG cases without skips, 1,601 frontend cases, and 217 engine
passes with 24 unchanged skips (no failures/errors, 1,454.999 seconds).
This supersedes the preceding pending status for jobs/scheduler storage. It
does not validate revision 0007 or turn skipped release cases into passes.

CI 43072322386 subsequently completed for `4d805c29` with all eight jobs
successful: 219 PostgreSQL cases without skips, 1,601 frontend cases, and
217 engine passes with 24 unchanged skips (no failures/errors, 1,474.771
seconds). This resolves the pending CI status for revision 0007. The engine
fixture remains the ordinary test engine; the skips and patched-FE acceptance
remain unproven. It does not certify the newer atomic configuration/logging edits.

## Atomic AI configuration and database log boundaries

Revision 0008 stores immutable function definitions, current heads, durable
registration jobs and outbox notifications in the same transaction as selected
provider/model/alias changes. Database guards reject missing or forged intent,
wrong job/actor bindings, duplicate independent registration and history edits.
The [contract](../../../architecture/production-refactor/PG_AI_CONFIGURATION.md)
keeps metadata commit distinct from native function installation. Native
participants, audit integration, caller ports and cutover remain open.

The combined PG17/PgBouncer run passes 285 cases without skips in 186.24 seconds,
including 43 new configuration cases and 47 identity-provisioning cases.
[ai-configuration-commands.json](ai-configuration-commands.json) records all nine
attempts. Earlier failures cover an UPSERT head guard, a fixture column grant,
a quoted test setting and the migrator fixture's missing logging defaults.
Assertions and runtime admission were retained while fixing those failures.

A broader scan found the synthetic invalid-integer canary in PostgreSQL's
primary error text. Earlier provider-only scans had missed that marker.
[The failed scan](ai-configuration-log-scan-attempt-1.json) remains in the record.
The fixed per-login policy suppresses raw SQL diagnostics, while Nova records
only fixed categories and validated SQLSTATE. Readiness and every unit of work
refuse policy drift; all 15 logins are tested. The final suite's six new server
log lines contain no credential marker or encrypted envelope. Raw logs are
not exported or purged. CI now has a credential-marker gate with 11 scanner
regressions, including primary-error, parameter-key and header markers.

Cleanup leaves no fixture schema or test roles. The real inventory refresh
preserves all 875 reviews, all 759 feature rows and previous live/API observations.
All 15 PG tables have ownership entries. There were no live provider calls or
engine mutations in this configuration test phase; live embedding QA remains
owner-waived. Production runtime wiring and the wider canary matrix remain open.

The final application tree
`4b9b8f0f39c61cd17d13668144457d235698c0758706341feaebe6b852e9d42c`
passes 10,741 backend unit cases (four warnings, 447.46 seconds, 65% combined
coverage), full-application mypy, Ruff on 76 changed Python files and all
54 agent scenarios / 189 checks. The obsolete unit attempt was interrupted
after 8,558 passes when scanner coverage expanded; it is not counted as a
completed gate. Current CI is pending. The preceding alias commit has passed
all eight CI jobs, including 219 PG cases, 1,601 frontend cases and 217 engine
cases; its 24 skipped cases remain unproven.
