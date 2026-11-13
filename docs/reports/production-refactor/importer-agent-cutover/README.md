# Importer and Agent writer repair checkpoint

Updated 2026-11-13, Asia/Jakarta. Branch `feat/production-control-plane`, based on
`e0b5599448b26065bf84815ca07e0ead724d2d48`. The repository commit containing
this checkpoint identifies the delivered repair source. Commit `e83aa09a`
was pushed; [CI 43303791124](https://github.com/dwickyfp/nova/actions/runs/43303791124)
passed seven jobs but failed the engine job. Follow-up caller and fixture fixes
were pushed in `6c4ae0e7`; [CI 43312594237](https://github.com/dwickyfp/nova/actions/runs/43312594237)
passed seven jobs but failed the engine job: 203 passed, 24 skipped, 12 failed
and two setup errors. The earlier 91 setup errors are gone. Commit `5d2091e8`
then passed seven jobs in [CI 43319470127](https://github.com/dwickyfp/nova/actions/runs/43319470127),
including all 969 PG17 cases without skips. Engine integration reached 214
passes, 24 skips, three failures and zero setup errors. Commit `ebd229bb`
passed seven jobs in [CI 43325948539](https://github.com/dwickyfp/nova/actions/runs/43325948539),
including 970 PG17 cases without skips. Engine integration reached 216 passes,
24 skips, one failure and zero setup errors. The remaining Studio consolidation
failure requires schema 0040 and recovery admission in 0041; validation is recorded below.
No development cutover or backend/workload startup has occurred. A frontend-only
preview reached the login screen; authenticated docs inspection awaits cutover. Existing StarRocks
metadata and payloads have not been modified by import rehearsal.

## Implemented source

- Agent memory: actor-bound projection/revision/evidence transaction, explicit
  tombstone and relearning, Outcome linkage and scoped shared projection.
- Missions: PG/CAS with independent public and storage revisions, atomic audit
  and concurrent projection checks.
- Automations: durable immutable occurrence/job/outbox, fenced claims and
  completion, exact MCP delivery intent before the call and receipt afterward.
  Unknown outcomes remain blocked for verification without advancing the schedule.
- Scheduled Quality grants and task actor binding. Retired Agent helpers that
  mutated source metadata are removed; imported IDs and bindings are preserved.
- Reviewed import contract for 163 table entries / 1,578 source columns,
  timestamp provenance, strict conversion, immutable chunk receipts, resume,
  complete source/target row/key digests, managed payload verification and
  cross-row semantic checks. Epoch publication checks the committed PG receipt.
- Task role-binding import trigger correction in migration 0038. Application
  startup no longer attempts StarRocks audit DDL for Autopilot or migration
  metadata DDL for ingestion. Retired migration source CRUD helpers are removed.
- News story writes check scope ownership before comparing revisions, preserving
  the existing 404 refusal for a different owner/role/security context.
- Engine integration fixtures now provision their own PG metadata schema and
  real workload logins. Ingestion runs in a separate process; proxy pools belong
  to the proxy thread. Task fixtures use claims/generations and retain run history;
  ML fixtures retain reservation tokens. Seed/restart/fault cases remain CI-only.

The follow-up writer audit found Superset author-role and publication writes,
Semantic View dataset mappings, preview/context reads, query history, user
last-login history and Object Browser/Explorer Task reads still using the source.
They now use the existing actor-bound PG owners. Intelligence startup hooks no
longer create retired source tables. These changes do not alter the import schema.

## Evidence and retained failures

Detailed evidence files are private local artifacts under `/tmp`; this report
contains no exported source rows. A restricted persistent copy of source export
03 and PG provisioning files is stored outside the checkout under the local
Nova data directory. This local copy is not off-host recovery acceptance. Rehearsals use the existing dedicated PG17.11 TLS
fixture at port 55432, with ownership-checked schema/role cleanup, alongside the
unchanged `docker` development project. No second engine stack was created.

| Check | Observed result |
| --- | --- |
| Frozen source export 03 | 163 entries, 1,009,174 rows; no schema drift/uncontracted table; manifest SHA-256 `3c702a82c96416d8d4beb548a663c06c46e03230f31cb93daf31d77c6f19824e` |
| Full rehearsal attempts 01/02 | Failed during task role-binding import: shared trigger addressed an absent `provenance` column. First attempt exercised a committed-chunk interruption before this failure |
| Full rehearsal attempt 03 | PASS for row counts, key sets/digests and full row digests on the disposable target. This predates final receipt/payload/semantic hardening and does not satisfy the final two-rehearsal gate |
| Full rehearsal attempt 04 | PASS with committed-chunk interruption/resume, immutable validation receipt, 453 payload references and cross-row semantic checks. A subsequent missing-reference refusal was added; the final importer still requires repeat rehearsal |
| Read-only managed payload scan 01 | 453 references verified; receipts digest `a580d400b4e7afb7b2cb64e363ba9e19c765339c78ff1277768296c60973071e` |
| Agent/import PG suite 05 | 59 passed, including interrupted/resumed import, duplicate occurrences, ciphertext/timestamps, payload manifest recovery and forged epoch-publication refusal |
| Pure import suite 05 | 120 passed; the earlier truncated-read fixture used the correct length accidentally, then was corrected and rerun |
| Legacy Agent helper replacement | 45 unit tests passed |
| Agent scorecard 02 | 54/54 scenarios, 189/189 checks |
| Agent eval 02 → 03 | First attempt: 357 passed, one obsolete StarRocks memory fixture failed. Updated actor-bound trajectory: 358 passed |
| Full application mypy 07 | PASS, 733 source files |
| Engine collection 03 | 241 engine cases collected without the former 11 collection errors; execution is still required |
| Selected engine cases 01/02 | Earlier attempts retained fixture identity/Redis errors and a cross-scope 404/409 defect; fixes are under retest |
| Selected engine cases 03 | Skipped because the local wrapper used incorrect port/environment names. Not a pass; corrected wrapper is under retest |
| Full unit/coverage 02 | 11,132 passed, 64% coverage |
| Full unit/coverage 03 | 11,136 passed, 64% coverage, 525.99 seconds; ingestion startup was subsequently updated, so final run 04 is required |
| Migration startup/connector/preflight/retarget/data-mover unit tests | 149 passed, including PG-unavailable refusal without engine metadata fallback |
| Managed payload unit tests 06 | 9 passed, including missing active-file/version/READY-model references and incomplete reads |
| Agent eval 04 / scorecard 03 | 358 passed / 54 scenarios and 189 checks passed |
| Metadata test image build 01 | PASS; packaged importer contract included. Rebuild on final source remains required |
| Docs content tests 01 | 13 passed; running browser inspection remains pending |
| Selected engine cases 04 | 20 passed, one namespace fixture column-order failure; corrected for run 05 |
| Full unit/coverage 04 | 11,138 passed, 64% coverage; a subsequent News scope guard is covered by final run 05 |
| Full PG17 suite 01 | 958 passed in 1,190.74 seconds; the later disliked-message scope and importer checks also passed in selected run 07 |
| Selected engine cases 05 | 53 passed, 19 failed: stale Task workload/API fixtures, Ontology exception class, and a real News scope refusal defect |
| Selected engine cases 06 | 67 passed, five retired Task repository API failures |
| Selected engine cases 07 | 72 passed, no skips. Includes real PG Task metadata/lowering, News, Ontology, Agent feedback scope, importer and the safe original engine selection |
| Agent eval 05 / scorecard 04 | 358 passed / 54 scenarios and 189 checks passed |
| Full application mypy 08 / changed-file Ruff | PASS |
| Metadata image builds 02/03 | PASS, including the final News guard and test fixture changes |
| Full unit/coverage 05 | 11,138 passed, 64% coverage in 484.18 seconds on final application source |
| Engine collection 06 | 241 engine cases / 1,228 total, no collection errors |
| Owned diff / docs links / credential scan | PASS; checksum-bound import contracts preserve exact bytes across Git checkouts |
| Final rehearsal 05/06 | PASS; interrupted/resumed imports, full row/key/payload and semantic verification, committed validation receipts. CI results are recorded separately |

The deployment owner confirmed historical engine timestamps always used
`Asia/Jakarta`; reviewed Python UTC writers retain UTC conversion. Six Semantic
View timestamp columns are bound to reviewed source row digests. Unknown clocks
remain refusals, not implicit Jakarta defaults.

## Remaining delivery gates

1. Local code gates, full PG17, selected safe engine cases and the metadata image
   have passed as recorded above. Authenticated `/docs` browser inspection waits
   for the cutover runtime; the preview currently redirects to sign-in.
2. Final rehearsals 05 and 06 passed with committed-chunk interruption/resume,
   1,009,174 rows, 163 entries, 453 payload references, semantic verification and
   committed validation receipts. Their validation digests are
   `b018da3153126e1bce5e9f6a9c0188786ecefbd53c6593252c0054f4c4426180` and
   `79a8ee7f066d0df641bca6f79abb8a352a14ff5034c156b0e36dfd8ee9a67ca0`.
   These receipts cover schema 0038 and are superseded by rehearsals 07/08 on
   schema 0039. Both completed with `VALIDATED`, zero blocking conflicts,
   verified payloads/semantics, and committed-chunk interruption/resume. Their
   validation digests are
   `02ba61a02f5cfce5e68a814398aee46ed3a1b58ca31abe6539f418d805f0e09d` and
   `57e22b78639e758c86216abffdbc3297f2f6909051f6a710e83a0927d1807ae3`.
   Both use source export 03; actual cutover still requires a fresh frozen export.
3. Fetch/integrate remote main; commit and push owned changes. Follow CI to a
   passing conclusion. The owner authorizes destructive seeded integration in
   CI; it must not reseed this development stack.
4. Verify a final writer freeze and backup, export/import the final source,
   validate and publish the epoch. Provision stable private runtime credentials,
   run every development role through `dev.sh`, and smoke-test the migrated app.
5. Continue the feature QA matrix, live AI and fault acceptance. Large external
   PoC load/24h soak and live embedding tests retain only their recorded owner
   waivers. Durable interactive consent/agent separation, production Compose,
   ML/sandbox isolation and off-host recovery remain broader architecture work;
   this checkpoint does not claim those are complete.

## Follow-up validation (2026-11-13)

- CI 43303791124 engine: 126 passed, 22 skipped, two failures and 91 setup errors.
  All 91 setup errors came from restoring `maintenance=false` before publishing
  a fixture epoch. The fixture now restores its prior maintenance state. Both
  Stream failures used synthetic consumption without a published generation;
  fixtures now import that legacy generation into their owned PG target.
- BI caller unit attempt 01: 101 passed, 26 failed, three errors from obsolete
  StarRocks mocks and incomplete session fixtures. Attempt 02: 137 passed.
- Real PG caller/bootstrap suite 01: 94 passed. Covers publication CAS and
  duplicate scope, cross-user preview refusal, author-role revocation/rollback,
  semantic mapping/context and query history filters/RLS/maintenance reads.
- Agent eval 06: 358 passed; scorecard 05: 54 scenarios, 189 checks passed.
- Full application mypy 10 and user-data system-access boundary scan passed.
- Full unit/coverage 06: 11,137 passed, one obsolete Stage test patched the
  removed query-service system connection. The replacement uses the Stage
  metadata owner and verifies authorization before secret resolution with a
  valid caller role. All 15 Stage cases passed. Run 07 passed 11,138 cases before the final Semantic View
  publisher-identity fix. Final run 08 passed 11,139 cases, 64% coverage,
  in 482.47 seconds.
- Semantic View synchronization retains the authorized admin/steward publishing
  another owner's view. Dataset mapping records the actual publisher; source
  ownership is unchanged. Owner/admin unit regressions passed (five cases).
  Full application mypy 11 and Agent eval 07 (358 cases) also passed.
- Metadata test image builds 04 and 05 passed; 05 includes the final publisher
  fix. User-doc content checks 02 passed all 15 cases.
- Full PG17 suite 02 passed 964 cases in 1,234.92 seconds. Additional PG run 01
  passed three cases: Task browser scope/body privacy, last-login history and
  the fixture's unpublished-epoch maintenance regression. There were no skips.
- Changed-file Ruff, documentation links/diff checks and runtime secret-canary
  scan passed. User-doc content checks 03 passed all 15 cases after the final
  author-role conflict guidance. `bash -n dev.sh` passed.
- Seeded/destructive engine integration remains delegated to CI under the
  owner's existing authorization. Final local checks above are complete; the
  follow-up commit still requires green CI before cutover.
- Receipt fixture attempt 01 against real PG refused a target-commit record
  without the dispatch claim. Attempt 02 passed after binding both consumption
  claims and recording each target commit before advancing its offset. The test
  also verifies refusal before claim acquisition and before the target-commit
  record. This is PG control-plane evidence, not engine execution evidence.
- Imported running Agent regression 01 passed on real PG: reading preserves
  incomplete history, a new login cannot heartbeat or finish without its claim,
  explicit stale interruption fences the old producer, and stored events and
  original start time remain intact.
- CI 43312594237 exposed stale Catalog actor calls, retired Studio bootstrap
  helpers, password-flag calls without actor context, and missing timestamps in
  the synthetic Autopilot verdict. Migration tests patched settings and policy
  methods in the API process while execution now belongs to an independent
  worker. They now send the real confirmation and temporarily change only the
  disposable fixture role's Ranger policies, retaining negative assertions.
  Idempotency also asserts successful job completion on both attempts.
- The follow-up audit found maintenance lineage reads filtered out by ordinary
  audit RLS, plus naive deployment-time watermarks interpreted as UTC. Revision
  0039 provides the fixed maintenance-only projection; manual callers keep audit
  RLS. A real-PG regression covers the narrow result, wrong identity/workload
  refusal, private direct audit access and repeated refresh across a historical
  Asia/Jakarta watermark. Attempt 01/02 refused an unsupported function-local
  custom parameter; attempt 03 passed with the existing fixed-projection policy
  pattern. Expanded attempt 04 passed all 89 Catalog/audit/migrator cases,
  including malformed bounds, all workload grants and manual caller scope.
- Final schema-0039 local checks: 11,139 unit tests passed with 64% coverage
  (run 09, 427.02 seconds); mypy 12 passed; Agent eval 08 passed 358 cases and
  scorecard 06 passed 54/54 scenarios and 189/189 checks. Importer/process PG
  run 09 passed 17 cases. Receipt recovery run 03 passed on the same schema.
  Metadata image build 07, docs run 05 (15 cases), changed-file Ruff, boundary
  scan, local links, diff and credential scan passed. Engine collection remains
  241 cases without collection errors. Full PG and seeded engine acceptance
  remain required in the next CI run; the prior CI PG job passed 967 cases.


The retained source snapshot was also inspected for interrupted work before
starting the cutover operator. Task graph/node runs, migration jobs, ML runs and
Notebook executions are terminal; Notebook runtimes are STOPPED/LOST/FAILED.
All six Dataviz publications are terminal (five SUCCEEDED, one FAILED_FINAL).
Four historical Agent runs still record `running`; no producer process remains.
Startup does not resume these turns or replay tools. Their source status and
events remain intact through import; scoped replay can interrupt a stale turn
without inventing missing history. There are no Agent automation definitions.

## Final access repair

CI 43319470127 retained two failures in the migration denial fixture: its
temporary policy edit used the JSON alias `policyItems` as a Python attribute.
The fixture now uses the typed model's `policy_items`; two unit cases verify
the requested restriction and complete restoration after an assertion fails.

The remaining Studio failure exposed a runtime caller defect. Agent access
verification rebuilt the Semantic View caller without its assigned roles, so
the PG reader refused the incomplete identity. Both the Verify Access route
and runtime recheck now pass the actual session assignments; neither invents
an assignment from the requested role. A real-PG regression verifies published
View access, preserved principal/login/role/version, and refusal for an
unassigned role. The controlled test mocks only governed source execution;
CI engine execution remains a separate gate.

Focused access run 01 retained one obsolete unit session expectation; run 02
passed all 153 cases after the fixture included its real assignment. PG access
run 01 passed the new regression. The schema/import contract did not change,
so rehearsal receipts 07/08 remain applicable. The expanded PG domain run 02
passed all 62 Agent/Semantic View cases. Full unit run 10 passed 11,141 cases
with 64% coverage in 414.49 seconds; mypy 13, Agent eval 09 (358 cases),
scorecard 07 (54 scenarios/189 checks), metadata image build 08 and docs run 07
(15 cases) passed. Changed-file Ruff, the user-data boundary scan, local links,
diff and credential checks passed. Seeded integration remains delegated to CI
under the owner's authorization; cutover awaits its final result.
These observations must be confirmed against the final frozen export; they do
not authorize replay of an uncertain external effect.

## Internal task handler repair

CI 43325948539 progressed through Agent access verification and exposed a
`409` while queuing knowledge consolidation. The task row guard still allowed
only `sql` and `python`. Migration 0040 adds the five existing internal handlers
from the task worker's finite allowlist. Applied revisions remain unchanged;
unknown handlers, handler mutation and stale definition revisions remain refused.

Focused PG attempt 01 reproduced the constraint refusal and retained an
incomplete Agent access fixture. Attempt 02 admitted consolidation once, but
the handler-mutation assertion expected the row guard instead of the earlier
column-grant refusal. The corrected Task/Agent/migrator suite passed all 108
cases in 128.12 seconds (attempt 03). Extending the consolidation regression to
execution then exposed a fixture workload mismatch: a task handler ran with
the web PG login (attempt 04). Both the PG regression and Studio engine fixture
now execute the handler with the actual task workload login. No application
grant is widened to make that fixture pass.

Focused attempt 05 passed both cases. The controlled provider path verifies
durable admission, source/session binding, one pending graph job, persisted
memory and exclusion on a repeated consolidation. Governed engine access is a
separate CI gate. The final schema-0041 results are recorded below.

Audit of automatic learning admission then found the task login could scan
pending messages but lacked the right to reconstruct their task definitions.
Migration 0041 grants insertion only for the canonical task orchestration
service, the consolidation handler and its exact pending source identity,
thread, Agent, owner, active role and security context. It grants no general
SQL/Python definition authority. Focused attempt 06 passed both API and worker
admission and the handler guard (three cases), including persisted learning
and duplicate exclusion. Negative cases cover a missing/foreign source, changed
session/role, other handler/schedule and a user actor. The final expanded run
also checks a different service principal and owner. Rehearsal 09 was stopped
on its owned disposable target when this additional fix was found; it is not
a pass. Cleanup completed and source data was untouched. Attempt 10 never ran.

Full unit run 11 passed 11,141 cases (64%, 440.51 seconds) on schema 0040.
Final schema-0041 run 12 passed 11,141 cases (64%, 455.58 seconds). Mypy 14, eval 10 (358 cases), scorecard 08
(54 scenarios/189 checks) and the boundary scan passed on unchanged application
source. Metadata image builds 09–11 passed; final build 12 includes schema 0041.

Expanded PG attempt 07 passed all 109 Task/Agent/migrator cases in 115.99
seconds. Final rehearsals 11/12 each validated all 1,009,174 rows, 163 inventory
entries and 453 payload references, including committed-chunk interruption and
resume, full row/key digests, semantic checks and zero blocking conflicts.
Their schema-0041 validation SHA-256 values are
`c99e5b3f6a244c616623cd937a460523d1c4c52a7b0a457d9338927e96a45b32` and
`dbd48c1429296b77641ef4ea579cc3b9fa78c0540249a9d44bc5a8dc88b4bdb1`.
These supersede the schema-0039 rehearsal receipts for cutover acceptance.

The launcher audit also found the old frontend/gateway routing sent all Agent,
assistant and Intelligence action requests to the query workload, which has no
Agent metadata grants. Those routes now use their web metadata owner; query/ML
retain their separate query process. Gateway session hashing is preserved on
the web upstream. Interactive Agent process separation remains unfinished;
this correction does not establish horizontal Agent scaling. Nine HTTP proxy
regressions verify destinations, path boundaries and preserved authorization.

Frontend install/browser setup, lint (zero errors, 31 existing warnings), build
and coverage passed: 195 files, 1,623 tests, 58.64% line coverage. The first build
attempt retained a Node/browser timer type collision when the new server test
lived in the application source tree; it now belongs to the separately typed
Node test project. Gateway syntax passed in its disposable nginx helper. The
first helper attempt lacked the capability to initialize its cache ownership;
the second passed with only CHOWN added. The existing split Compose validates
with the original Docker project directory and private development environment.
No gateway service was started and no shared infrastructure was recreated.

Changed-file Ruff, boundary scan, diff and local links passed. Final image build
12 passed. CI on this final source, actual epoch publication and development
startup remain pending. The owner-authorized CI execution must retain its
separate seeded/destructive environment.

## Scheduler startup and monitoring follow-up

A source review after `39f5c47a` found that the standalone scheduler factory
still passed the historical Redis lock to its polling loop and no PG lease to
`SchedulerTick`. Due reservations would be refused despite a live process.
The factory now constructs one PG lease and shares it with both owners. The
real-PG factory regression proves one winner across two scheduler instances,
one graph/job/outbox across repeated ticks, and handover without duplication.

Cluster Monitor now reads the task scheduler's PG lease under the requesting
admin identity. Migration 0042 grants web only the lease name/deadline columns;
forced RLS excludes other leases and non-admin/SERVICE actors. Holder IDs,
fencing tokens and writes remain unavailable. PG failure reports an unknown
scheduler signal even when Redis responds; Redis failure does not hide a live
PG lease. Worker heartbeat behavior is unchanged.

| Follow-up check | Result |
| --- | --- |
| Scheduler unit 01 | 90 passed |
| Scheduler PG 01 | 15 passed, one fixture failure: its attempt to rewrite a creation timestamp was correctly refused. The test now waits for a real short interval |
| Scheduler PG 02 | 16 passed, including production factory contention/handover |
| Scheduler PG 03 | 17 passed, including admin projection and forbidden holder/token/write access |
| Scheduler/monitoring focused unit 01 | 156 passed |
| Tasks/scheduler/migrator PG domain 01 | 65 passed, 66.45 seconds |
| Full application mypy 16 | PASS, 733 files |
| Agent eval 12 / scorecard 10 | 358 passed / 54 scenarios and 189 checks passed |
| Docs browser 02 | 15 passed across two files |
| Metadata image build 13 | PASS, schema 0042 included |
| Full unit 13 | Interrupted after 6,368 passes to include the monitoring correction; not a completed gate |
| Full unit 14 | 11,143 passed, 64% combined coverage, 460.98 seconds; branch measurement is enabled in `pyproject.toml` |
| Schema 0042 rehearsal 13/14 | 13 VALIDATED with interruption/resume; 14 running on the disposable PG target. Schema 0041 receipts remain historical |

Root/scoped operations guides now describe the actual PG owners, web Agent
routing, default split `dev.sh` startup and operator-provisioned schema/epoch.
Notebook live queues/leases, interactive consent and production Compose remain
explicit unfinished boundaries. No runtime application process or cutover epoch
has been started by this follow-up at this checkpoint.

## Outbound AI credential follow-up

The post-commit live-smoke preparation found a remaining caller/grant mismatch:
`AIService.get_provider_api_key` used the canonical `nova-ai` SERVICE actor,
while the raw credential policy accepted web administrators or the agent login.
The real-PG facade regression reproduced a missing key on web before the fix;
query and background inference owners also lacked direct table grants.

Migration 0043 adds a fixed `ai_runtime_credential(text)` projection for the
reviewed invocation workloads under exactly that SERVICE identity. The function
requires an active, non-deleted provider and a matching credential revision.
It returns ciphertext to the existing internal decryptor. Direct-table grants
remain unchanged, public execution is revoked, and USER actors plus unrelated
workloads are refused. API provider/model projections still contain no stored
key. This restores existing invocation paths; standalone Agent execution and
ML isolation remain separate unfinished architecture work.

| Follow-up check | Result |
| --- | --- |
| Provider PG 01 | Reproduced web SERVICE resolution returning no key; retained failed attempt |
| Provider registry/migrator PG 02 | 60 passed, 50.41 seconds; nine runtime workloads, inactive/missing providers, wrong actors, unrelated workloads and existing direct-table refusals |
| Provider focused unit 01 | 155 passed |
| Full application mypy 17 | PASS, 733 files |
| Agent eval 13 / scorecard 11 | 358 passed / 54 scenarios and 189 checks passed |
| Docs browser 03 | 15 passed |
| Full unit 15 | 11,143 passed, 64% combined coverage, 465.05 seconds; CI branch-coverage command |
| Schema 0043 rehearsals 15/16 | Both VALIDATED: 1,009,174 rows and 453 payload references, with committed-chunk interruption/resume |

Schema 0042 rehearsals 13/14 both validated before this follow-up. Their receipt
SHA-256 values are `2ddb81b135c4e9855e1c5078058fd8d1ae10061bfd914fd4e2a7d0c07cdddf53`
and `3b3189e940a3c8cf2b3846b8899676a9faaed2d67bb81c899172b1fdda411c47`.
Commit `0d37d227` is pushed; its CI is
[43336883729](https://github.com/dwickyfp/nova/actions/runs/43336883729).
The preceding `39f5c47a` run was cancelled by supersession, not passed.
No actual development cutover has occurred at this checkpoint.

Final schema 0043 validation receipt hashes: `d010e903115d1304f1f113944e7c466a56e7bae31e534fec3ba487709984026f`, `febef80c0835bf60dc005e1e5d95299469d71782f8a32338953436142fb04503`.

CI `0d37d227` PG executed 975 cases: 973 passed, two failed because the test image lacked `fakeredis`. The two Agent consolidation cases remain mandatory. Moving the already locked `fakeredis[lua]` dependency into the `test` group fixes image installation; all 149 package versions and the runtime/host-dev dependency graphs are unchanged. Image build 15 and the exact image regression passed: 47 cases in 50.90 seconds.


## Scheduled Agent dependency follow-up

CI `0d37d227` engine finished with 216 passes, 24 skips and one failure
(no setup errors). Task-owned consolidation reached real Agent verification,
which could not read custom-tool definitions. Its previous focused PG case
mocked that verification and therefore missed the workload grant boundary.
The improved case reproduced the same 503 locally before the fix.

Migration 0044 gives Task read-only access under the existing custom-tool RLS
and selected release-manifest policy. Owner and active shared-role membership
still determine visibility; unrelated tools/manifests and definition writes
remain refused. Consolidation now exercises actual grant/fingerprint checking
in its real-PG regression. Three focused cases pass, including revocation and
cross-user scope. No permission assertion or integration case was removed.
The first expanded domain invocation used a nonexistent test filename and
collected no tests; the corrected invocation is recorded separately.

Final follow-up checks: Agent/provider/migrator real-PG domain 02 passed 122
cases in 144.22 seconds; schema/import unit 01 passed 124 cases. Full application
mypy 18 and changed-file Ruff pass. Image build 16 includes schema 0044. The
11,143-case unit/coverage run, 358 eval cases and 54/189 scorecard results above
cover unchanged application source and resolved host dependencies. The complete
seeded engine suite is delegated to CI under the owner's existing authorization.
A read-only source check at 01:00:46 UTC found all 157 source tables unchanged.
Schema 0044 import rehearsals and actual cutover remain pending.
