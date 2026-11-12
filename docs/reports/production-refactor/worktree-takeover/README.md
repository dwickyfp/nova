# Worktree integration in progress

Updated 2026-11-13. Delivery checkout: `feat/production-control-plane`, starting
at `c1837b082b93efae7828fd32e9bdf184aa8eb61e`. The owner explicitly authorized
integration of the seven stopped `cutover-*` and `port-*` worktrees. Original
patches, untracked files and base identifiers were copied to a private local
snapshot before merging; the source worktrees remain intact.

This is an implementation checkpoint, not release acceptance. No metadata epoch
has been published and no development data has been cut over. The integrated
source is still under repair and must not be deployed yet.

## Evidence before integration

- Full unit/branch coverage: 10,948 passed, four warnings, 64% coverage (420.94 s).
- Full application mypy: no errors.
- Existing Docker project and volumes reused; no additional stack created.

These results cover the starting source only. They do not validate the imported
worktree changes or the final delivered source.

## Integration findings and changes

- Combined seven worktrees and resolved overlapping imports. Unapplied graph-job
  migration was renumbered from 0053 to 0027 after the cutover journal at 0026;
  27 placeholder migrations and an ad-hoc schema-dump test were excluded.
- Unified request actor and nested transaction ownership. Added checks for
  principal, role, login, security version, read-only nesting and task-local
  connection ownership; child tasks inherit identity, not a PG connection.
- Found incomplete Catalog, Notebook, ML and runtime caller ports. Adapters and
  worker wiring are being completed; remaining StarRocks metadata callers still
  block cutover.
- Changed normal development startup to separate web/query/proxy and supervised
  workload processes. Added port collision checks, readiness deadline, private
  logs, failure exit codes, loopback bindings and longer graceful drain.
- Added disposable-process launcher tests: failed startup, readiness refusal,
  duplicate ports, descendant cleanup and a six-second worker drain all pass.
  This is not evidence of a successful live application startup.

## Attempts retained

1. Initial integrated collection: 9,198 collected, 23 collection errors.
2. After caller/import repairs: 10,737 collected, 10 collection errors.
3. Initial runtime selection: 97 passed, nine fixture errors.
4. Runtime and launcher selection: 120 passed, three Search-port failures.
5. First real-PG selection: five fixture connection failures (the disposable
   database has PUBLIC CONNECT revoked). Fixture logins now request explicit
   CONNECT instead of relying on PUBLIC.
6. Second real-PG selection: 76 passed, five migrator-fixture failures with the
   same connection-grant assumption; corrected before the third run.

7. Collection after import/caller fixes: 10,998 unit tests collected, no errors.
8. Second full-unit diagnostic stopped at its 400-failure limit: 5,124 passed,
   179 failed, 221 errors. Many fixtures still targeted removed StarRocks stores.
   This is a failed attempt, not release coverage. Subsequent focused fixes are
   listed separately; this is not the final full-suite result.
9. Real PG17, migrator/catalog/notebook/ML/task/stream/job selection: 198 passed.
10. Real PG17 domain selection: 619 passed, one PgBouncer prerequisite skip.
11. PgBouncer prerequisite supplied: 50 passed across role process lifecycle,
    transaction pooling, stage scope and ML metadata. This includes the previously
    skipped stage pooling case. No additional Docker stack was created.
12. Focused caller/dev/ML selection: 113 passed. Cutover/actor/ML regressions:
    71 passed. Intelligence selection: 189 passed. History pagination/API:
    23 passed. These selections overlap; their counts are not an aggregate.
13. Frontend lint: zero errors, 32 warnings. Build passed. Coverage: 194 files,
    1,614 tests passed; statements 57.8%, branches 55.83%, functions 52.59%,
    lines 58.65%. Browser and live feature acceptance remain outstanding.
14. Complete unit diagnostic after the earlier fixture fixes: 10,359 passed,
    485 failed, 177 errors (215.43 s). Subsequent Catalog/isolation/AI/News and
    Notebook fixes need another full-suite run; this failed attempt is retained.
15. Assistant isolation, Explorer and account flags: 66 passed. AI function
    rendering/consumer, HTTP role startup and maintenance supervision: 117 passed.
16. Real PG durable queue plus AI function participant: 39 passed (59.89 s).
    Cases include lost acknowledgment after external commit, cancellation after
    commit followed by observation-only recovery, engine refusal, unknown outcome,
    wrong prompt and kind-filtered claims. The engine in these participant tests
    is controlled; this is not patched-FE or live-provider acceptance.
17. Full-application mypy passed for 728 files before the subsequent cutover,
    history and account-flag repairs. It must be rerun on the final source.
18. Fourth unit diagnostic: 10,731 passed, 306 failed, 13 errors (237.23 s).
    Fifth diagnostic: 10,936 passed, 116 failed, five errors (243.91 s).
    These are failed attempts; later focused repairs do not replace full coverage.
19. Task and migration selection: 218 passed. Workspace selection: 37 passed.
    Notebook/ML selection: 142 passed. Provider registry: 68 passed. Agent pin
    selection: 55 passed. Migration/catalog selection: 77 passed. Learning trace
    and migration lease selection: 32 passed. Selections overlap and are not summed.
20. Real PG17 task, stage, audit and semantic-view selection: 152 passed
    (177.45 s), including abandoned task receipt retention and batched scheduler
    occurrence lookup. No source epoch activation or development-data reset.
21. Seventh mypy run passed for 728 files. Subsequent migration heartbeat,
    workspace, learning-trace and test changes require another final run.

22. Sixth full-unit diagnostic: 11,023 passed, 38 failed, four warnings
    (237.88 s). The remaining fixture and implementation repairs were rerun in
    domain selections; these overlapping counts are not summed.
23. Full unit/branch coverage attempt 07: **11,065 passed**, four warnings,
    64% coverage (420.10 s). This covers the integrated source before the later
    consent-identity and agent-registry changes below; it is not final-source
    acceptance. Changed-file Ruff attempt 05 and application mypy attempt 09
    passed. Mypy attempt 08 caught an incorrect monitoring UoW return shape,
    which was repaired before attempt 09. Agent scorecard: 54/54 scenarios,
    189/189 checks.
24. Monitoring/assistant/access-control/BI real-PG attempts 08 and 09 each passed
    38 cases. Attempt 09 (43.91 s) uses the installed application pool and actual
    metadata UoW for the new AI-usage checks, rather than replacing the scope.
25. Consent identity attempt 01 was invoked from the wrong directory and failed
    in the unrelated system Python environment before collecting Nova tests.
    Attempt 02: 145 passed, three failed. After porting expectations and adding
    boundary regressions, attempt 03: 184 passed, one warning (7.46 s), including
    MCP trajectories. No external provider or server is used in these fixtures.
26. Agent registry caller port is now under validation. Initial focused run:
    174 passed, 12 failed, five fixture errors (4.75 s); the failures still
    target removed StarRocks behavior or need investigation. Mypy attempt 10
    found one optional-owner narrowing error, repaired before the next run.
    Real-PG attempt 10 passed 35 cases (36.03 s); the next focused unit attempt
    passed 191 cases (4.52 s). This port invalidates reuse of the prior green
    full-unit and mypy results as final-source evidence.

27. Real-PG agent attempt 11: 37 passed (43.67 s). Added concurrent facade edits
    that roll back the losing snapshots, concurrent release capture producing
    one immutable manifest, and rejection of a mismatched saved configuration.
    Model/resource discovery is controlled in the release fixture; this is not
    live AI acceptance.
28. Full-unit diagnostic 08: 11,099 passed, two failed, four warnings (249.58 s).
    Both failures were release fixtures still patching the removed Redis/SR
    publication path; they now exercise the PG transaction/agent lock boundary.
    Full unit/branch coverage attempt 09 passed 11,106 tests with four warnings
    and 65% coverage (424.17 s). Later research/share/quality caller changes
    require the next full-source coverage run.
29. Full eval attempt 01 stopped on a removed Intelligence schema import.
    Attempts 02 and 03 each had one failed lifecycle case (363 and 366 passed)
    because their fixtures lacked the new actor arguments. The corrected
    lifecycle selection passed four cases; eval attempt 05 with the new role
    identity, semantic-usage and release regressions passed 370 cases (10.03 s).
    Scorecard attempt 02: 54/54 scenarios and 189/189 checks. Application mypy
    attempt 12 passed 728 files. Changed-file Ruff attempt 07 passed before the
    subsequent dispatch/test edits and must be refreshed.
30. Offline assistant benchmarks: 11 passed; the engine benchmark was skipped
    by its stack/prerequisite guard. That skip is not engine acceptance. Docs
    browser component tests: two files, 15 passed (2.43 s). Opening `/docs` on
    the migrated live application is still required.

31. Research/proposal port: 18 focused unit tests passed (1.68 s). Real-PG
    attempt 12 passed 40 cases (42.26 s), including typed immutable JSON,
    concurrent research updates and a one-winner proposal decision. Mypy
    attempt 13 passed all 728 application files.
32. Share/candidate port: focused attempts 04 and 05 each passed 25 cases.
    Real-PG attempt 13 passed 81 cases (91.92 s) with migration 0029. The
    conversation projection refuses stale/inactive shares, keeps the viewer's
    identity and removes attachments, instructions, result rows and private
    interactive inputs. Direct message-table access remains refused. Mypy
    attempt 14 passed all 728 files.
33. Quality caller port attempt 01 selected a nonexistent test path and collected
    no tests. Attempt 02 found an import indentation error; mypy attempt 15
    found the same error. Both are failures. After repair, attempt 03 passed
    169 cases with one failed foreign-role fixture. Binding that fixture to
    its switched role before the call preserved the expected refusal; attempt
    04 passed all 170 cases (1.69 s). Mypy attempt 16 passed 728 files.
34. Changed-file Ruff attempt 08 reported 16 formatting/import findings across
    the 349 changed Python paths. Those checks were pending at that checkpoint; the later results, including
    the retained unit-10 failure and PG14 pass, are recorded below.

Research, rule proposals, verified-query candidates, share management and
Quality Lab records now use the actor-bound PG stores. Research updates lock
one journal row before CAS; terminal runs cannot resume. Share creation locks
one owner/object scope so concurrent requests cannot duplicate a target or
exceed the quota. Quality updates retain immutable evaluation inputs and use
PG revision conflicts; their ordinary writes no longer rely on a Redis lease.
Monitoring reads the caller's role/version-scoped assistant traces from PG.
The scheduled Quality handler still needs its workload ownership and grants
reconciled: `task_orchestration/internal_handlers.py` dispatches it in the task
role, which does not have the Agent-store permissions. Existing Redis locks
around multi-step monitoring configuration and feedback analysis also remain.

The [remaining Agent system-call inventory](remaining-agent-system-calls.csv)
records source locations, including legacy helpers and calls needing manual
classification. It is not a count of confirmed active runtime writers. The
main remaining Agent areas are automations, Missions, memory and historical
resource delegation. The refreshed source inventory contains 55 call sites,
including historical helpers; it is not a count of active writers. Registry semantic backfills
must move into the one-shot importer; they must not be run against mixed stores.

AI Usage now reads assistant counters and query audit from PG under the caller's
RLS. The Agent run journal no longer drops its claim or reports completion when
its fenced finish is refused or commit acknowledgment is unknown.

Pending tool approval now requires the original principal, login, active role
and security version. Foreign/stale decisions do not consume the pending entry
or create a read grant, including secure input and uploads. New proposals after
a role switch capture the new context. This closes the process-local identity
hole; durable cross-process consent and restart recovery remain unfinished.

The agent registry, version and release facades now use PG. Prepared
migration 0028 refuses retained grants on private agents, recognizes the public
custom-tool name prefix and permits platform MCP administration under the human
ACCOUNTADMIN identity. It has not been installed on the development authority.
Tool dispatch now binds each invocation to the current context, so a role switch
does not leave subsequent metadata work under the previous role. The original
run actor remains unchanged for its fenced journal. Semantic usage preserves the
full bound login and refuses conflicting caller fields.

Source legacy backfill methods, remaining agent domains and runtime separation
must still be reconciled; this is not completed Agent migration.

Workspace tombstones retain object references, but failed post-commit object
cleanup is currently best-effort and is not picked up by a durable cleanup
consumer. Keep this recovery gap open even when deletion unit tests pass.

The cutover publication command now requires a caller-supplied digest and the
complete packaged import contract, matching freeze ID/time, migration ledger,
source inventory, target mapping, counts, key sets and row digests. Missing
contract files still refuse publication. This guard does not supply the missing
export/import/rehearsal implementation or prove source/target equality itself.

The maintenance role now polls the durable AI registration queue with one
operation admitted at a time, a 240-second operation deadline inside a 300-second
lease, and supervisor-visible PG failure. HTTP startup no longer registers
functions or reports an unverified match. Receipts compare the complete function
expression through Nova's parser, including prompts and parameters, rather than
only checking for an alias token/model/endpoint substring. Runtime consumer tests
check shutdown, timeout, missing Redis and no requeue after cancellation.

The account password-change flag now propagates a metadata outage rather than
letting a login bypass a recorded change requirement.

An abandoned task now retains its engine query identifier and error under the
lease fence. Scheduler planning reads already-reserved occurrences in batches;
new occurrences still require the atomic PostgreSQL reservation.

Workspace save treats only a confirmed missing object as absent. An unavailable
object store no longer triggers a replacement write. Notebook edits pass the
metadata revision to the workspace compare-and-swap operation and report the
winning document revision on a conflict. ML persistence preserves uploaded
artifacts when rollback cannot be confirmed.

Migration workers now cancel and drain the operation when their heartbeat,
lease or original login fails. A replacement login cannot reuse the earlier
job's credentials. These regressions verify cancellation; they do not establish
external-effect reconciliation for every migration statement. Learning-trace
writes refuse malformed existing history instead of replacing its evidence.

ML training now verifies the original login fingerprint, cancels on lease loss
or identity change, drains training/heartbeat tasks on shutdown, and preserves
actor context. Canonical metadata transactions refuse cross-actor nesting and
never share a live connection with a child task. These checks do not complete
ML artifact isolation, all durable consumers or horizontal-scaling acceptance.

Private local logs and XML are retained under `/tmp/nova-takeover-*` (mode 600).
They identify the attempt and command group. All results above use the uncommitted
integration checkout based on the starting commit; final commit/image/schema
release evidence is not yet available.

The PostgreSQL fixture contained abandoned, tagged test roles with no schema,
owned objects or active sessions. Cleanup verified their exact deployment marker
and database-only ACL dependencies before removing those roles. Development
StarRocks, Redis, object storage and their volumes were not reset.

## November 13 integration checkpoint

- Outbox publishing now has its own supervised process and default `dev.sh`
  startup/readiness port. It claims and commits in PG before sending Redis
  event/job identifiers, then acknowledges under the claim fence. Redis loss,
  duplicate wakeups, cancellation and unknown acknowledgments are covered by
  controlled tests. The publisher neither claims nor executes domain jobs.
  Docker wiring gives it PG/outbox and Redis configuration without StarRocks
  credentials. This is prepared wiring, not a live deployment or a completed
  migration of every producer/consumer.
- Agent usage/session observability now uses the caller-bound PG repository.
  Totals and daily points use the same UTC calendar-day window. Traces reject
  another agent or owner before loading messages.
- Artifact and historical tile facades now use PG. BI pointer changes preserve
  opaque IDs, check the client's timestamp and use the row's revision CAS.
  Historical visualization specs are retained by migration 0030; application
  requests and the conversion CLI no longer alter/drop schema columns. Old
  tests that required runtime DDL were replaced with no-StarRocks-call,
  CAS, retained-definition and real-PG regressions.
- Shared dashboards retain viewer identity and expose only charts referenced
  by the granted stored layout. Worksheet field shelves and categorical values
  survive projection. Direct cross-owner reads and revoked/inactive-role grants
  are refused. This metadata evidence does not establish governed engine or
  Superset publication acceptance.
- The conversion CLI discovers names through a restricted maintenance
  projection and separately authorizes each delegated owner before conversion.
  End-to-end CLI/Superset conversion still needs the migrated runtime.

Additional retained attempts:

- Unit coverage 10: 11,109 passed, four failed, four warnings (529.09 s).
  The four timezone fixtures bound a different role/version from the tested
  caller. They now bind the intended complete identity; the focused runtime,
  publisher and timezone selection passed 91 tests (1.66 s). Full coverage 11 passed 11,122 tests with four warnings and 65% coverage
  (532.02 s). After the shared-trace fix, coverage 12 passed 11,123 tests with four
  warnings and 65% combined coverage (566.37 s).
- PG14: 83 passed (103.20 s), including Quality facade and identity-scoped traces.
  PG15 collected no cases because a requested process filename did not exist.
  PG16: 46 passed, three fixture errors (UUID passed where the process API
  requires an epoch string). PG17 passed 49 tests (174.29 s) after the fixture
  fix; setup failures also close the pool.
- PG18: one failure, 44 deselected. The new observability test tried to set a
  historical timestamp outside maintenance, and the DB correctly refused it.
  The fixture now enters maintenance for its import-only timestamp adjustment.
  PG19: two passed, 44 deselected (2.79 s), covering the runtime usage/artifact
  facades on schema 0030. PG20 stopped in filename preflight without running
  pytest. PG21 passed 944 cases with two engine cases skipped (1,251.96 s). The
  skipped cases used default test ports and both passed in the separate
  existing-stack read-only run described below. Skips remain recorded as skips.
- Artifact/Superset/viz/sharing/thread-isolation selection: 101 passed (1.22 s).
  Observability isolation selection: four passed. These overlap full suites.
- Frontend coverage attempt 02 was interrupted (exit 130) during resource
  pressure and retained a conversation-history failure. Attempt 03 used two
  workers: 194 files, 1,614 passed (124.08 s); statements 57.81%, branches
  55.83%, functions 52.59%, lines 58.64%. No failed attempt was reclassified.
- Frontend lint attempt 02 hit a generated coverage-file removal race. ESLint
  now ignores generated `coverage/**`; source checks remain enabled. Lint 03
  passed with zero errors and 31 warnings. Build 03 passed after the artifact/usage documentation edits. Docs browser
  components passed two files/15 tests (4.56 s); this is not a live `/docs`
  acceptance run.
- Ruff11 found 14 style/import errors; Ruff12 found three remaining long SQL
  literals. Ruff13 passed for 364 changed Python paths after formatting and
  splitting the same SQL literals. Full-application mypy19 passed 729 files.
  Eval07 passed 358 trajectories (12.42 s, one warning); scorecard04 passed
  54/54 scenarios and 189/189 checks. Eval counts exclude unit regressions
  included in earlier combined selections.
- Development and split Compose validation both passed after adding outbox.
  No changed image was built or started. `bash -n dev.sh` passed; controlled
  launcher/readiness/shutdown tests pass. Live `dev.sh` startup remains blocked
  by the missing all-domain cutover/epoch, rather than bypassing its guard.

The final shared-trace review found that the Python helper rebuilt its allowed
trace fields from the original value after recursively stripping results. A
new regression reproduced the nested result/preview leak (one failed case,
eight deselected); the helper now uses the stripped value. The fixture also
checks that source messages remain unchanged. Artifact/sharing selection 02
passed 102 cases (1.91 s); coverage 12 passed 11,123 tests, mypy20 passed 729 files, eval08 passed
358 trajectories (13.28 s), Ruff14 passed 364 changed Python paths, and
scorecard05 passed 54/54 scenarios with 189/189 checks.
The PG conversation projection already used stripped values. No live leak
or live cross-user acceptance result is claimed by this controlled regression.

The read-only StarRocks connection-cancellation selection passed two cases
(1.06 s) using the existing development FE and configured control credential.
It runs only constant SELECTs through test-owned TCP connections, and proves
that a cancelled response does not become the next borrower's query result.
It does not reseed data, stop dependencies or establish governed-user policy
acceptance.

Required engine/BI integration fixtures still contain pre-cutover schema setup
and cleanup (for example `test_studio_business_metadata.py`,
`test_governed_studio_metadata.py` and legacy Superset conversion fixtures).
They must be reconciled with the final PG/engine topology before CI acceptance;
unit and PG storage passes do not make those suites ready. No destructive
engine fixture or reseed was invoked locally. The
[runtime observation](runtime-observation.json) records existing container image
IDs and the 30 prepared migration checksums without environment values.

## Still required

- Finish all caller/writer ports, durable consumer wiring, immutable effect
  recovery, consent, runtime admission, ML/sandbox boundaries and production
  Compose. Count remaining old authority accesses from source, not prepared
  repository names.
- Complete importer package/contract, timestamp provenance, resumable import,
  validation/publication contract, both rehearsals and one-way cutover.
- Complete updated unit/coverage, mypy, changed-file Ruff, eval, real-PG and
  applicable governed-engine tests; validate dev.sh on the live installed epoch.
- Complete required browser and CI checks on the final source; reuse the unchanged
  frontend gate results only while its source/configuration stay unchanged. Follow destructive
  integration in CI under the owner's existing authorization.
- Resume the feature matrix and live AI acceptance after architecture gates.
  The owner's load/soak and live-embedding waivers remain waivers, not passes.

Full migration/scaling and whole-feature QA remain incomplete. Do not replace
this status with a completion claim until the final evidence supports it.


Final local results for this checkpoint are in the
[machine-readable QA summary](qa-summary.json). The PG fixture cleanup check
confirmed no test schema, test roles or deployment marker remained. All 15
existing Docker project containers were still running. The original source
worktrees remain preserved. There is no delivered commit, push, CI result,
production epoch or full live application startup to report.


## Checkpoint publication requested on November 13

The owner explicitly requested commit/push, cutover and development startup.
This authorizes publishing the reviewed checkpoint while the existing CI
exception remains in effect for seeded/destructive integration. It does not
make the missing importer, caller ports or release gates pass. The checkout
contains no packaged `metadata_import/contract.json` or timestamp provenance,
and the recorded Agent writer inventory still includes old-authority calls.
Epoch publication and full dev startup remain blocked until those technical
prerequisites and validated migration are complete. No empty epoch or stale
metadata fallback is permitted as a substitute.

Before publication, remote `main` was fetched and was already an ancestor of
the active branch. The generated, tracked `backend/coverage.xml` remains local;
it is excluded from the implementation commit. Test evidence above is the
completed local validation of this source; CI outcome is recorded separately.
