# Migration, scaling and remaining QA

Updated 2026-11-13 (Asia/Jakarta), through the stopped-worktree integration
and Agent caller-port checkpoint ([source and evidence](../../reports/production-refactor/worktree-takeover/README.md)).
The owner requested this checkpoint before further work: finish migration and
application scaling first, then resume the remaining whole-feature QA. Tests
needed to validate an architecture change still run with that change.

The stopped worktree ports were integrated and published in checkpoint
`01209dbbd58bf6690fb865169aa85244a6a9647b` on `feat/production-control-plane`.
See the [takeover checkpoint](../../reports/production-refactor/worktree-takeover/README.md)
for baseline results, failed integration attempts and remaining work. The source
is not yet a deployable cutover. The attempted development startup refused
incomplete metadata configuration before starting any application process;
the existing infrastructure and source data remain in place.

## Completion decision

**Neither the metadata migration nor application scaling is complete. The
production refactor is not ready for release.** Passing foundation tests and AI
checks do not establish a migrated or scalable production deployment.

| Area | Verified state | Remaining implementation |
| --- | --- | --- |
| Metadata authority | Runtime domains still use StarRocks `NOVA_SYSTEM`; PG17 transaction, identity, migration and storage boundaries are tested | Port every domain and writer, finish import/provenance, rehearse twice, freeze all writers, validate final import, activate PG epoch and cut over once |
| Ownership inventory | 163 source-table entries and 875 reviewed system call sites | 153 entries have tested PG storage with caller ports pending and ten have prepared import-only schemas; none remains `NOT_STARTED` and no entry records completed cutover |
| PG schema | Revisions 0001–0030 prepare deployment state, preferences, password flags, AI registry/aliases/definitions, jobs/outbox, scheduler fencing, audit, retained history, Stages, Workspace ownership/shares, Workspace file entries/version receipts per-person Notebook runtime/execution/package metadata, Task definitions/edges/runs with version CAS and lease fencing, Stream control claims/receipt chains, and ML jobs/models/versions with leased jobs and immutable versions; Catalog inventory with sync-generation fencing and ciphertext-only credentials, business Ontology with append-only revisions, Agent definitions with CAS and a fenced run journal, Intelligence (Semantic Views, revision stores, Search, entities and feature store), cutover-operation journals and Task graph job links, Query autopilot approvals, leases and fenced results, and assistant threads, access-control policies, BI/Dataviz publications, migration jobs and LLM usage metering | Caller transactions in every runtime writer, refusal-audit integration, import provenance and whole-import validation |
| Durable jobs | Queue, outbox, leases/fencing and receipts pass real-PG fixture tests; AI function registration is wired to maintenance; the standalone outbox publisher commits claims before Redis notifications and fenced acknowledgment, with recovery cases tested on PG | Remaining producers/consumers, participant verification and recovery; Redis must cease being authoritative for work |
| Agent runtime | Conversations and fenced run/event journals are ported to PG; process-local approvals now bind principal, login, active role and security version. Registry/version/release, research, rule proposals, candidates and sharing callers use PG and pass facade/concurrency fixtures; Quality Lab and owner-scoped usage/session observability use PG and pass facade tests. Artifact/tile readers and BI pointer updates use PG/CAS; shared dashboards use a bounded member projection. Shared conversations use a restricted projection under the viewer identity | Memory, Missions, automations and legacy caller/import reconciliation; scheduled Quality workload grants; durable consent and read grants, standalone agent process, exact effect intents, reconnect and restart recovery |
| Workload processes | API supports `all`, `web`, `query`; eight workload roles now have separate processes and supervised default startup; maintenance owns Search builds, Catalog/News refresh, ML retention and durable AI function registration; a starting role now waits up to 180 s for an unavailable engine, Redis or held metrics port instead of exiting and taking every sibling down with it, and supervised roles stop when their launcher exits | Remaining interactive agent separation; per-role admission, dependency readiness, fenced claims, isolation and validated limits across the full deployment |
| Execution security | Existing delegated SQL and Ranger path retained | Isolated ML execution/provenance, notebook/Python/MCP credential and network isolation, atomic role changes and revocation boundaries |
| Production deployment | Existing engine/development/split Compose files remain | One production Compose with all required services active, scoped inline environment anchors, stable private secrets, pinned images, TLS/private management ports and adjustable resources |
| Recovery | No production recovery acceptance established | PG base backup/WAL, off-host copy, coordinated recovery manifest, restore/PITR and dependency recovery tests |

Source evidence: [ownership](METADATA_OWNERSHIP.csv),
[call inventory](SYSTEM_CALLS.csv), [entrypoints](ENTRYPOINTS.csv),
[dependencies](DEPENDENCIES.md), [traceability](TRACEABILITY.md).
`backend/app/core/config.py` still restricts API roles to `all/web/query`;
`backend/app/worker/__main__.py` now owns one selected workload, while its default
launcher supervises eight child processes. Prepared PG
repositories are not proof that their runtime callers use PostgreSQL.

## Implementation order from this checkpoint

1. Complete domain DDL, provenance and transaction ports against the reviewed
   ownership matrix. Preserve IDs, owners, ciphertext and historical rows; do not
   substitute a generic SQL dialect-rewrite proxy or production dual-write.
2. Connect durable job/outbox/receipt and consent mechanisms to real producers
   and workers. Uncertain external outcomes enter verification; lease loss must
   not replay a mutation. Keep analytical Stream journals in their data commit.
3. Finish workload separation and supervision, per-role admission/readiness,
   sandbox/ML boundaries and one-Compose production wiring. Raising resource
   limits or replica counts alone does not complete scaling.
4. Finish backup/restore and migration tooling. Perform both rehearsals,
   including interrupted import, then verified one-way cutover. After new PG
   writes, recovery uses PG and receipts, never stale StarRocks metadata.
5. Resume the full QA matrix below against the migrated runtime. Re-run affected
   checks after every subsequent code or deployment change.

## Remaining implementation with concrete owners

The takeover source is prepared but cannot start the full migrated application
until these paths agree on authority and recovery:

| Component | Required change before cutover | Owning source |
| --- | --- | --- |
| Agent memory and learning | Port projection, revisions and evidence in one transaction; preserve deterministic IDs, repeat evidence, deletion/relearning and Outcome linkage; restrict shared-domain projections | `backend/app/modules/agents/memory.py`, `knowledge_router.py`, `metadata_repository.py` |
| Missions | Port admission, stage/run projection, resume and deliverables; fence concurrent writers and verify recovery | `backend/app/modules/agents/mission.py` |
| Automations and scheduled Quality | Port due scan, claim and completion; reconcile execution-role grants and authorize each owner; do not replay unknown tool effects | `backend/app/modules/agents/automations.py`, `backend/app/modules/task_orchestration/internal_handlers.py` |
| Historical Agent helpers | Move semantic/skill backfills to the importer; reconcile retained delegation data without restoring removed product modes | `backend/app/modules/agents/repository.py`, `resource_delegation.py` |
| Durable runtime ownership | Standalone interactive agent using the existing loop; PG consent/read grants; Notebook queue replacement; durable workspace cleanup and remaining consumers | `backend/app/worker/`, `backend/app/modules/assistant/consent.py`, Notebook/workspace owners |
| Import/cutover | Package the reviewed contract and timestamp provenance, implement resumable whole import and semantic validation, rehearse twice, freeze/drain/validate/activate once | `backend/app/core/metadata_import*.py`, `metadata_export.py`, missing `backend/metadata_import/` package |
| Production operations | One complete production Compose, scoped stable secrets, separate Redis workloads, TLS/private ports, ML/sandbox isolation and provenance, coordinated off-host backup/restore | `docker/`, ML/Notebook/backup owners |
| Integration harness | Replace pre-cutover metadata setup/cleanup and bind actual PG processes without reseeding development; validate final CI, live providers and governed engine | `.github/workflows/ci.yml`, `backend/tests/integration/` |

Memory compatibility needs particular care: the legacy upsert uses a deterministic
memory ID and retains history after deleting the projection. The prepared PG
revision guard currently requires an existing projection for revisions after 1,
while projection inserts must start at 1. A deleted/relearned fact therefore needs
an explicit, tested restoration path during the caller port; a happy-path
`create_memory` fixture does not establish that behavior. Source evidence:
`AgentMemoryRepository.upsert/delete` and migration 0021's
`agent_knowledge_guard`. This is an unported-path finding, not a reported failure
of the current StarRocks development runtime.

## Whole-feature QA still outstanding

The [759-row feature matrix](FEATURE_QA.csv) remains the detailed worklist.
Its current results are separate from unit-test counts:

- Happy paths: 731 `NOT_RUN`; remaining entries include bounded pre-cutover
  passes, failures and one owner waiver.
- Invalid input: 754 `NOT_RUN`, four `PASS`, one owner waiver.
- Authorization: 129 `NOT_RUN`, 626 partial entries, three `PASS`, one waiver.
  An authenticated-route inventory does not prove object/role isolation.
- Dependency failure: 753 `NOT_RUN`; the remaining non-waived entries contain
  partial checks, retained timeouts/stalls and unresolved failures.

| QA area | Required remaining evidence |
| --- | --- |
| Identity/governance | Login/logout and concurrent role changes; users/roles; Ranger enforcement, masks/filters, shares, audit/refusal, revocation and cross-user access on the migrated runtime |
| Data/SQL | Explorer, DDL/DML, views, stages/file query/load, catalogs/connections, migration, Pipes and MySQL proxy; permission and dependency-failure cases |
| Tasks/Streams | SQL/Python tasks, schedules, DAG/finalizer, overlap, cancel/retry, every schedule/WHEN combination, offsets on skip, receipts and crash recovery |
| Workspace/Notebook | File revisions/shares, concurrent edits, SQL/Python cells, novaduck, packages, output limits, interrupt/restart/reconnect and runtime isolation |
| Studio/Dataviz | History and replay, artifacts/charts, publication repair, dashboard permissions, assistant behavior, cancellation and dependency failures |
| AI/ML | Provider/model configuration, aliases, AI SQL, search, agents, tools/MCP, skills, semantic views, ontology, feature store, training/prediction, error/timeout/rate-limit/malformed-response and prompt-injection contracts |
| Operations | Settings/monitoring/usage, maintenance refusal, fresh install/upgrade, backup/restore, restart, quotas and controlled shutdown |
| UI/docs | Desktop/mobile, keyboard/focus and loading/error states; all applicable SQL/Python examples executed against the final migrated runtime |
| Concurrency/faults | Revision CAS, duplicate submissions, fencing, role switching, consent replay, notebook edits, worker death around external commit, Redis loss, PG restart, object storage failure and quota exhaustion |
| Sandbox/security | Host files/socket, database/Redis/internal network/metadata endpoint/other runtime access; canary scans across logs, responses, queues and artifacts |

## Defects and incomplete evidence to retain

The [risk register](RISK_REGISTER.md) remains authoritative for individual
findings. In particular:

- The earlier two-login approval probe failed and remains in the
  [baseline](../../reports/production-refactor/proposal-identity/pending-context-baseline.json).
  The integrated broker now binds principal/login/role/version; controlled
  unit/eval cases pass for decisions and secure uploads. Live acceptance on the
  final runtime is still required, and durable consent remains incomplete.
- Pending consent remains process-local. Durable PG consent, crash recovery,
  atomic consent consumption and mid-turn remote revocation are incomplete.
- Earlier live AI/MCP/Studio timeouts, a stalled run and one creation failure
  after commit remain recorded. New passing attempts do not erase them.
- Workspace CAS, notebook package identity, Task orphan reconciliation,
  migration execution identity, cache isolation, historical timestamp/namespace
  provenance and uncertain usage commits remain in the risk register.
- `AI_EMBED` has an unresolved registration-template contract gap. The live
  embedding waiver does not establish implementation correctness.
- UI QA on 2026-11-12 (see [the checkpoint](../../reports/production-refactor/workspace-caller-port/README.md)
  and [the open-findings record](../../reports/production-refactor/open-findings/README.md)):
  the CDN editor dependency, pipe listing, sandbox start timeout, UDF create/drop
  and docs, and four slow endpoints are fixed. The engine shows its recycle bin
  only to root, so Nova refuses that listing explicitly. With the owner-approved
  60-second per-role visibility cache, `ontology/metrics` takes 5.5–8 s warm
  (16 s cold) and `ontology/lint` 1.7 s warm; one page reload did not recur.
- Historical Agent runs with missing events remain preserved; their provenance
  is unresolved. Do not fabricate events or silently discard history.

## Existing validation to reuse only when applicable

The Workspace files checkpoint passes 571 real-PG tests (40 new Workspace file
cases), 10,909 unit/coverage tests, 358 evals, the 54/189 scorecard, mypy,
changed-file Ruff and the user-data boundary check. It prepares two more stores;
Workspace/Notebook runtime callers still use StarRocks. Today's PG log window is
clean, but the full fixture log retains 26 credential-canary matches from
2026-11-11 whose source test is not yet identified.

The Workspace sharing checkpoint passes 531 real-PG tests (42 Workspace cases),
10,909 unit/coverage tests, 358 evals, the 54/189 scorecard, mypy, changed-file
Ruff and the user-data boundary check. The initial eval failure and its corrected
text-versus-UUID assertion are retained in the linked evidence. This prepares
two additional stores; it does not migrate Workspace/Notebook runtime callers.

The maintenance checkpoint passes 10,909 unit/coverage tests (zero skips),
358 evals, the 54/189 scorecard, 84 focused tests, mypy, Ruff on 129 changed
Python files and 12 monitoring tests. Local metrics confirm Search polling in
maintenance and none in the API. Seven worker roles each report one fresh
instance; API readiness/frontend return 200 and the proxy listens. The first
probe's missing host-service prerequisite and its restoration remain recorded.
See the maintenance evidence linked above. Delivered-commit CI is still required.

The workload-separation checkpoint passes 10,895 unit/coverage tests (zero
skips), 358 evals, the 54/189 scorecard, mypy, Ruff on 125 changed Python paths,
55 focused lifecycle tests and 12 monitoring checks. Six dedicated host worker
processes each report one healthy instance on the existing stack. Both affected
Compose configurations validate. Container image startup, complete dependency
readiness and replica safety remain unverified; this is not final scaling
acceptance. The earlier sandbox-constrained unit attempt is retained alongside
the complete passing rerun in the linked evidence.

At `97012355`, local backend checks pass 10,859 unit/coverage tests, 358 agent
trajectories, the 54-scenario/189-check scorecard, mypy and Ruff on 115 changed
Python files. Eleven offline benchmarks pass; one engine benchmark is skipped
because its fixture expects a disposable-stack label. The skip is not a pass.

The latest protocol/approval fixture passes six real-provider SQL-tool turns,
three per configured model, with explicit approval, known SQL result, output
schema and persisted history. Four controlled API flows also pass. These cover
those flows only. See [proposal identity evidence](../../reports/production-refactor/proposal-identity/README.md).

The preceding 489-case real-PG result remains applicable to unchanged PG code;
the 1,609-case frontend result applies to unchanged frontend application code.
Updated docs independently pass 15 browser tests and were viewed at `/docs`.
These are pre-cutover results and must not be reported as final migrated-feature
acceptance.

CI for `c71ba4b1` passed eight jobs: 486 PG tests and 217 engine passes, with
24 engine skips retained. CI for `2b441d80` and `3f6b3789` also passed all eight jobs,
with 489 PG passes and 217 engine passes / 24 skips each:
[latest verified result](../../reports/production-refactor/ci-3f6b3789.json).
The subsequent maintenance and both Workspace checkpoints still need their own
delivered-commit CI results. The
ordinary CI engine is unpatched and does not establish governed acceptance.

## Owner-approved exceptions and safe execution

- Live embedding-provider QA is `OWNER_WAIVED`, not `PASS`. Do not configure a
  new embedding provider for QA. Retrieval paths requiring embeddings retain
  their missing live prerequisite.
- The original medium-PoC concurrency target and 24-hour external-host soak are
  waived. Test on this device, record actual load/capacity, and make production
  resources adjustable without claiming unmeasured capacity.
- One-host Docker OpenSandbox is accepted. Multi-host HA and host isolation
  certification are not claimed; boundary tests remain mandatory.
- Reuse the existing development stack. No local reseed, dependency chaos or
  duplicate supporting stack. Destructive integration/fault tests belong in
  the already-authorized disposable CI environment.

Release completion requires the remaining architecture, migration and applicable
QA gates to be satisfied. This document is a continuation checkpoint, not a
release approval or a claim of zero unknown bugs.
