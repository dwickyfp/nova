# Intelligence, job and monitoring persistence

## Revision stores

`IntelligenceRepository` resolves table names through `ENGINE_TABLES`, a fixed
map of fourteen control tables. Its reads and writes are metadata operations.
Most reads bind principal, active role and security version. Shared story,
decision and action-review lookups deliberately delegate authorization to their
owning service; PG needs a narrow service policy for these paths rather than a
global RLS bypass. A guessed ID must not overwrite another security scope.

The source key is `(id, revision, operation_id)`. Multiple operations at the
same revision are preserved and readers reject the fork. Do not migrate this to
`UNIQUE(id, revision)` by silently selecting one branch. Profile forks, preserve
each operation identity and stop import with a redacted conflict manifest until
each conflict is resolved. New writes need a transactional expected-revision
check; renewing a Redis lease before an INSERT is not that check.

Record payload timestamps come from `utc_now()`, while the SQL row's
`created_at` uses engine `NOW()`. Convert each field by its producer, retaining
the serialized payload contract. News feedback is keyed by user/story and uses
engine time. Business policies preserve both `default` and historical
`default:<fingerprint>` IDs; their same-revision conflict refusal remains.

The Intelligence bootstrap also initializes fixed Business Event, feedback and
policy tables. Business Event tables use the existing ontology revision-store
shape. Their schema DDL belongs in the migrator; their higher-level writers and
authorization still require separate review.

## Autopilot and long jobs

`AutopilotRepository` and its schema resolve only thirteen fixed
`QUERY_AUTOPILOT_*` tables. Preserve IDs, family/cohort IDs, version/state,
payloads and expiry. The UPDATE path already checks `expected_version`;
insert-only needs a PG uniqueness conflict path. Source timestamps are Python
UTC-naive. Cleanup is limited to observations/rollups/baselines and must not
erase durable action decisions. API/worker scope enforcement is outside this
repository and remains part of authorization QA.

The eleven ML-job and ten migration-job system calls target
`CONFIG_ML_JOBS` and `CONFIG_MIGRATION_JOBS` respectively. These are control
records, not the trained artifact or migrated data. Preserve actor, role,
security version, session fingerprint, operation/request, cancellation and
result fields. ML SQL is encrypted persistence; copy ciphertext without
exposing it in a manifest and preserve the existing encryption key.

Submission writes a short-lived session link to Redis before inserting the
job. The current durable row alone cannot recover that identity after Redis
loss. Claims and completion compare status but have no monotonically fenced
claim token. Stale jobs become interrupted; an interrupted migration may
already have external effects. The PG design must atomically store immutable
request/identity references, job and outbox, keep session validation fail
closed, and reconcile external receipts before retrying. Do not resurrect a
logged-out session or add a service-account fallback to achieve recovery.

ML retention currently deletes terminal rows after one day, including
interrupted jobs. Retention must preserve unresolved receipts, artifact lineage
and required audit evidence. Migration retention already excludes execute and
execute_batch; keep that distinction. These writers use engine time.

## Monitoring split

Audit query/history/statistic reads in `MonitoringRepository` belong in PG.
Process lists, native task histories/schedules, load records, FE/BE metrics,
compactions and backup repository observations stay in the engine observer.
`kill_query` is a restricted engine mutation and must retain its authorized
caller and audit. None of these engine observations should be translated into
PG metadata SQL merely because they share the old system pool.

AI usage joins assistant history and reads audit/LLM usage rows, all PG control
metadata. It currently treats assistant timestamps as UTC-naive but converts
engine audit/usage timestamps with `CONVERT_TZ`. Remove that source-specific
conversion only after the imported PG fields have a verified UTC meaning.
Monitoring pagination/counts/time buckets and retention need PG query plans and
indexes; the read inventory is not measured latency or privacy acceptance.
