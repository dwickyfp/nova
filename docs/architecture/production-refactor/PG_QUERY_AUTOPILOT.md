# Query Autopilot metadata port

Revision 0023 prepares the thirteen `QUERY_AUTOPILOT_*` tables in `nova_meta`,
with `AutopilotMetadataRepository` as the prepared replacement for the
`NOVA_SYSTEM` paths of `AutopilotRepository`. Running API, proxy, scheduler and
autopilot worker code still uses `NOVA_SYSTEM`; nothing imports the repository.

Every table keeps the source envelope: `id`, `family_id`, `cohort_id`,
`version`, `state`, `payload`, `created_at`, `updated_at`, `expires_at`.
Payloads are stored as the source writes them, so opaque ids, family and cohort
digests and model fields survive import unchanged. Rows carry `provenance`
(`runtime` or `imported`).

## Workloads

The source has four process owners. The autopilot worker has no PG login of its
own; [WAVE_J](WAVE_J_REVIEW.md) assigns it to the `automation` workload, so the
`automation` login holds its grants.

| Process | Login | What it does here |
| --- | --- | --- |
| API (`web`) | `nova_meta_web` | Policy and enrollment changes, proposals, approve/reject, requested jobs, listings, ontology join history; flushes its own telemetry |
| API (`query`), MySQL proxy | `nova_meta_query`, `nova_meta_proxy` | Flush buffered telemetry: observations and opt-in sample evidence; read the policy and enrollments |
| Scheduler | `nova_meta_scheduler` | Queues the `aggregate` and `cleanup` jobs after each tick |
| Autopilot worker | `nova_meta_automation` | Claims jobs, runs experiments, applies and verifies, aggregates, enriches, retention |

| Table | Read | Write |
| --- | --- | --- |
| Policies, enrollments | web, query, proxy, automation | web |
| Opportunities | web, automation | web (propose, approve, reject, withdraw), automation (lifecycle) |
| Jobs | web, scheduler, automation | web (requested operations), scheduler (housekeeping), automation (claims, follow-ups, checkpoints) |
| Experiments, actions, outcomes | web, automation | automation, under the job lease |
| Evidence | web, query, proxy, automation | web, query, proxy, automation; automation deletes expired rows |
| Observations | web, query, proxy, automation | web, query, proxy insert; automation age retention |
| Incidents | web, automation | automation (insert once) |
| Families, rollups, baselines | web, automation | automation; rollups and baselines age out |

`agent`, `task`, `ingestion`, `ml`, `notebook`, `maintenance` and `outbox` have
no grant: maintenance owns sweeps, not the Autopilot executor. All access needs
a bound actor (`nova.service_identity` equal to the login); the migrator writes
only as `nova_meta_owner` while the deployment is in maintenance.

## Rules the store enforces

**Governed definitions.** A policy or enrollment changes only from the version
that was read; the change advances the version by one and the payload must carry
the same id and version. `updated_by` is the bound principal. Changes are
audited.

**Candidates and approvals.** A candidate is proposed once, unapproved, at
version 1. Every change advances `metadata_revision` from the revision that was
read; two concurrent changes give one success and one conflict. The candidate
`version` (advanced by an experiment) moves by at most one. Approval:

- only the `web` workload approves, only from `READY_APPROVAL`, with an
  unexpired approval and a payload whose `approved_by` is the bound principal;
- the store records `approved_by`, `approved_role` and
  `approved_context_version` from the transaction's actor, not the request;
- an approval cannot be moved, re-attributed or carried into a new candidate
  version; it can only be withdrawn, and a withdrawn approval is not restored
  by setting the state back;
- `APPLYING` needs a current approval or `READY_AUTO`; only a person rejects.

Proposals, approvals, rejections and lifecycle changes write a success audit
row naming the new state.

**Jobs.** A job is created once by id. The id of a requested operation is the
source's idempotency digest; reuse with a different kind, candidate or request
hash is a conflict. Requested operations are attributed to the bound actor; the
scheduler can only queue `aggregate`/`cleanup`, the worker only follow-up
`verify`/`apply` and housekeeping. Only the worker claims: a claim moves a
queued job to `RUNNING` and advances `lease_generation`; an interrupted run can
be retaken for reconciliation, which also advances it. Only the current holder
heartbeats or finishes, finished jobs (`COMPLETED`, `BLOCKED`) are frozen, and a
state change advances the job version. In-request approve/reject jobs are
completed by `web` without a lease. Worker cursors (`CHECKPOINT` rows) stay
cursors. Only completed `aggregate`/`cleanup` jobs are purged.

**Fenced results.** Experiments, actions and outcomes carry `job_id` and
`job_generation`. A write is accepted only while that job is `RUNNING` at that
generation; the job row is locked `FOR SHARE`, so a re-claim either waits for
the write or makes it fail. A worker that lost its lease cannot record an
intent, acknowledge an application, write an experiment or record an outcome.

**Actions.** The intent (`APPLYING`) is recorded before the engine change and
must name the statement digests, candidate binding and actor (compensations and
sandbox clean-ups name the object binding they remove). It is acknowledged once
(`APPLIED`): the acknowledgement may only add to the intent, and an applied
object (MV, index, refresh policy, plan baseline) must carry its verified object
identity. An identical retry is accepted, a different one refused, and nothing
is deleted. PG cannot commit the engine change and the acknowledgement
atomically: an intent without an acknowledgement stays `APPLYING`, and the
caller verifies the engine object instead of re-submitting.

**Facts.** Outcomes are recorded once; only a pending rollback resolves, once.
Observations are inserted once with the observation time as `created_at`; an
identical retry is accepted, a different one refused, batches are bounded at
128. Incidents keep the first detection, as the source's insert-only write.

**Evidence.** Identity (kind, source, collection time, family, cohort) is fixed.
Evidence still `unavailable` may be completed; collected evidence can only
expire; expiry is final and must drop the payload reference. Only expired rows
are deleted.

**Sensitive text.** Query text never enters these tables. Replay samples stay
encrypted in the payload store; evidence keeps the reference and a parameter
digest, observations keep the canonical shape the source keeps. Job payloads
keep `actor_session_id` as the source does; API reads redact it.

## Findings not fixable in storage

- The autopilot worker shares the `automation` login. A dedicated
  `nova_meta_autopilot` login needs bootstrap credentials, Compose wiring and
  the bootstrap role check, which are outside this storage revision.
- The source writes candidates with blind upserts serialized only by Redis
  locks, and acknowledges actions and outcomes by overwriting the row. The
  caller port must carry `metadata_revision` and use the intent/acknowledge and
  fenced APIs; the store refuses the overwrites.
- The generic listing route returns every collection (other cohorts' families,
  jobs with actor ids) to any reader. RLS here is per workload, not per cohort;
  per-person scoping stays an API authorization item. The overview route counts
  `NOVA_SYSTEM` tables directly and must be ported too.
- Imported `APPROVED` candidates have no bound approval (the source keeps only
  `approved_by` in the payload). They cannot move to `APPLYING` until a person
  approves again. Imported `APPLYING` actions have no lease; they cannot be
  acknowledged and need engine verification and operator reconciliation.
- Source timestamps are UTC-naive Python values; import as UTC is assumed and
  its provenance is not verified.
- No source path re-queues a `BLOCKED` job, so it is treated as final; confirm at
  the caller port. The source's 20-minute stale-run rule uses `started_at` and a
  Redis lock; the port must heartbeat the PG lease.
- Telemetry is buffered in process memory and flushed only by API roles and the
  proxy; a crash drops the buffer.
- Ontology join history (`web`) and the Intelligence autopilot read families and
  must be ported with the other callers.

## Remaining work

Port every reader and writer at cutover (router, service, worker, telemetry,
aggregation, evidence, acceptance, scheduler hook, ontology and Intelligence
readers), the import with its conflict manifest, and recovery for uncertain
applications and compensations.
