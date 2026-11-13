# Requirement traceability

Updated 2026-11-13 (Asia/Jakarta). The accepted plan and
[acceptance contract](ACCEPTANCE.md) govern this delivery. Single-host operation,
the reduced local load target and the live embedding waiver retain exactly the
owner-approved scope; none establishes enterprise isolation or cross-host HA.

The [importer/Agent delivery record](../../reports/production-refactor/importer-agent-cutover/README.md)
contains current-source results, retained failed attempts and cutover evidence.
The [worktree takeover record](../../reports/production-refactor/worktree-takeover/README.md)
is historical. Test totals across checkpoints overlap and must not be added.
See the [delivery backlog](DELIVERY_STATUS_AND_QA_BACKLOG.md) for remaining owners.

| Requirement | Work packages | Evidence needed | Verified state and remaining gate |
| --- | --- | --- | --- |
| Complete metadata ownership and writer inventory | WP00 | Live schema, caller review, migration mapping | 163 source entries and 1,578 columns have an executable import contract. Follow-up reviews ported Agent, BI, history and Catalog maintenance callers. Complete dynamic coverage remains part of feature QA. |
| Consistent architecture instructions | WP01 | Root/scoped rules and operations docs agree with runtime | Root/scoped contracts distinguish target architecture from verified deployment. Delivery and operator records must track the actual activation. |
| PG-only transactional metadata | WP02–07 | DB01–15, domain regressions, no stale metadata path | Schema 0044 and runtime caller ports use actor-bound PG transactions, workload grants, RLS and revision CAS. Local and CI results are linked above. Final import, epoch activation and normal dev readiness passed on schema 0044. Runtime QA found follow-up Notebook, Agent deletion and telemetry defects; their candidate repair is tracked in the delivery record. |
| Lossless schema translation | WP03 | Per-column provenance, constraints, duplicate retention and full import checks | Explicit type/clock contract, duplicate identity preservation, immutable chunks, exact resume, full row/key digests, payload receipts and semantic validation are implemented. Rehearsals 11/12 validated 1,009,174 rows and 453 payload references on schema 0041, including interruption/resume; schema 0042 repeats also passed; schema 0043 repeats also passed and are recorded in the delivery report. Schema 0044 rehearsals 17/18 and the final frozen import also passed; final receipt and digests are in the delivery record. |
| Durable jobs and effects | WP08, WP13 | JOB01–23, crash matrix, engine receipts, Redis loss | PG queue/outbox, immutable definitions, leases/fencing and receipts are implemented and tested for migrated owners. Agent automation records exact MCP intent before delivery. Unknown effects remain blocked/under verification. Remaining participant reconciliation and full external fault acceptance are open. |
| Explicit workload processes | WP09 | Admission, supervisor, cancellation, resource isolation | `dev.sh` launches web, query, proxy, scheduler and eight supervised workload processes, with role readiness and bounded shutdown. The scheduler factory shares one PG lease with occurrence reservation; admin monitoring reads its restricted PG projection. Agent/assistant HTTP routes use the web metadata owner, with session affinity; query/ML use the query process. Interactive Agent separation, full admission/isolation and production deployment acceptance remain open. |
| Durable Agent consent/replay | WP10 | Restart, role revoke, foreign approval, repeated tool invocation | Run/event journals, memory, Missions and automations use PG. Controlled tests cover stale producers, revision conflicts and login/role/version-bound approvals. Interactive consent remains process-local; durable consumption and restart/revocation acceptance remain open. |
| Notebook and ML execution boundaries | WP11 | Sandbox network/host/token tests, signed-model tampering, process isolation | Metadata and image/kernel checks do not establish execution isolation. Credential/network boundaries and ML artifact provenance acceptance remain open. |
| Role/session security | WP12 | Atomic role race, principal binding, active-admin gates, governed regressions | Actor/login/role/version isolation and malformed-context refusal have real-PG coverage. Patched-engine/Ranger integration and whole-feature role/revocation acceptance remain distinct gates. |
| Single production Compose, no `.env` | WP14 | Fresh start, upgrade, secret checks, private ports, role health | Incomplete. Local development uses the existing Docker infrastructure and host processes; it does not prove the approved production Compose contract. |
| Recovery and observability | WP14–15 | Base backup/WAL, restore, external manifests, alerts, faults | Private source exports and rehearsal receipts are retained locally. A local PG base backup with streamed WAL and copied-file verification passed; restore/PITR, coordinated external manifests and off-host copies remain required. |
| Safe one-way cutover | WP16–17 | Two rehearsals, aborted import, writer freeze, post-PG recovery | Both schema 0044 interrupted/resumed rehearsals passed, followed by the final frozen import and receipt-bound epoch publication. All process mappings passed readonly/write-refusal checks before maintenance was cleared. Normal runtime readiness, API/SQL, Notebook and bounded live AI smoke tests passed, with retained failures and follow-up repairs recorded. After new PG writes, recovery must use PG and receipts. |
| Every feature, including live AI | WP18 | Feature matrix plus UI/API/SQL and provider evidence | [Feature QA](FEATURE_QA.csv) remains incomplete. Three live chat and three configured SQL-tool trials passed after the recorded fixture corrections; this bounded evidence and unit/eval checks do not establish whole-feature acceptance. Only the recorded live embedding waiver applies. |

Missing prerequisites, skipped mandatory checks and owner waivers are not passes.
A green CI run or successful local cutover does not close the remaining production
architecture, fault, security or whole-feature acceptance gates.
