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
