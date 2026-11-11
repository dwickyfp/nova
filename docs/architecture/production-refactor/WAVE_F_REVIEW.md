# Studio workflows and knowledge persistence

The remaining Agent and Assistant system calls are control metadata. Dynamic
Mission references resolve through `ENGINE_TABLES`; quality records through the
five-entry `QUALITY_TABLES`. `HarnessRepository._run_rows` has one caller, reading
`CONFIG_AGENT_RUNS`; its schema inspection and additive DDL also target that
table. Resource delegation initializes the fixed resource/grant definitions.
These helpers do not authorize arbitrary SQL through the future PG pool.

## Atomic boundaries and identity

Memory has a mutable projection, immutable revisions and separate evidence.
Review can update the projection separately from its revision/evidence writes.
PG must transact these writes together while preserving operation IDs and
same-revision fork refusal. Legacy backfill creates private hypotheses, not
verified shared knowledge. Shared domain memory requires the existing semantic
access checks; a broad owner bypass would expose private revisions/evidence.
Memory projection timestamps are Python UTC-naive, whereas revision/evidence
row timestamps use engine `NOW()`. Payload timestamps retain their own offsets.

Mission reads distinguish the full session/security scope from an owner/role
scope used to discover resumable work. Resume explicitly changes the session
and security version under a revision comparison. Preserve this reauthorization
path; neither a new login nor a PG import may silently reactivate an old run.
Mission changes and deliverable receipts need atomic revision/admission checks.
The external artifact or publication still needs its own recovery receipt.

Automations store owner, role, schedule, condition, delivery and next-run state.
UTC-normalized schedule timestamps must retain the separately stored timezone.
The global due scan belongs to the automation process, while per-user CRUD
remains owner-scoped. `record_run` updates by automation ID without a fencing
token; PG job admission and completion must prevent a stale worker advancing
the schedule. Retired Smart/Auto agent automations stay disabled.

Verified-query proposal deduplication currently reads before inserting. Add a
transactional uniqueness rule for an active proposal after profiling existing
duplicates. Rule adoption, model-version snapshots and proposal status are a
single metadata operation. Immutable agent configurations and release manifests
retain their IDs, fingerprints and secret-screening rules; saving the same ID
with different content must remain a conflict.

## Reading and sharing

History uses owner-filtered keyset pagination with `(updated_at, thread_id)`
and `(seq, message_id)` ordering. PG indexes and imports must preserve those
ties and cursor semantics, not replace them with unstable offset ordering.
Agent observability first selects authorized threads, then loads their messages;
PG policies must enforce the same boundary on both sides of those reads.

`ShareRepository.visible` still expands assigned roles, whereas
`active_grants` uses the active security scope. This is an explicit migration
and authorization review item: do not implement the wider predicate as a generic
PG RLS rule. Shared transcript projections strip result rows; that projection
does not itself establish permission to run its saved query.

Deep-research updates and candidate decisions rely on service-level ownership
checks before ID-only writes. Their PG transaction must bind the verified
actor/role and reject a cross-scope ID. Quality records and monitoring preserve
principal, active role and security version, including JSON security-context
filters on assistant messages. Translation cache records are deployment-wide,
keyed by language/message/version, with Python UTC-naive timestamps; only the
translation worker should write them. No provider secret belongs in that cache.
