# Foundation verification checkpoint

This is partial evidence on the active migration branch, not release acceptance.
No domain has switched its metadata authority to PostgreSQL. WP00 transform and
grant review, complete schema/import migration, domain ports, production Compose
and cutover remain open. The foundation schema installer is verified separately.

## Environment and source

- Baseline commit: `c6a51ff2e5a94a46191884ded40fc15394de27ed`.
- Branch: `feat/production-control-plane`, active original checkout.
- Application source digest at these checks:
  `489df16dc4f4b9fa20169642ea44d0a3273116ae87ce4e58d9478401073e72d9`.
  This is the ordered path/content digest produced by the inventory script,
  recorded before the foundation checkpoint commit.
- Existing local Docker project: `docker`, OrbStack/aarch64, eight CPUs and
  approximately 8 GiB Docker memory. Development data was not reseeded.
- One additional PG17 fixture service in that same project; PostgreSQL 17.11,
  image digest `sha256:665dc7b37ac52a17e298511414c21d23ffa4e6a6ef6225b3e5d59c7afb35e994`.
  Connections use verified TLS. Only fixture-owned synthetic schemas/roles are
  created and removed. This is not the production deployment.

## Local results on 2026-11-11

| Check | Observed result | Scope |
| --- | --- | --- |
| Backend unit and branch coverage | 10,415 passed; 65% | Complete final checkpoint unit suite, including API/metrics and scanner regressions (516.47 seconds) |
| Full application mypy | Passed | `uv run mypy app --no-error-summary` |
| Changed Python Ruff | Passed | 29 changed/new Python files; final checkpoint diff |
| Agent eval | 54/54 scenarios; 189/189 checks | `uv run python -m tests.eval.report`; controlled trajectories, not live-provider proof |
| Full agent trajectory suite | 347 passed | Controlled providers and governed tool fixtures; now also a required CI step |
| PG foundation and configuration | 25 passed | 11 real PG tests plus 14 configuration tests |
| Task recovery regressions | 79 passed | Worker, reconciler and worker-service unit tests |
| Bootstrap/process ownership | 28 passed | Lock failure refuses bootstrap; role startup duties preserved |
| Inventory scanner | 6 passed | Dynamic calls stay unresolved; injected/aliased adapters; review/evidence preservation |
| API authentication inventory | 1,223 passed | 611 session operations, each with missing and invalid bearer, plus explicit non-session allowlist |
| API inventory plus HTTP metrics regressions | 1,226 passed | Nested route prefixes, aliases and scrape exclusion; auth handler sentinels cover effective routes |
| Frontend lint/build | Passed | 0 lint errors, 31 warnings |
| Full frontend coverage | 1,593 passed across 192 files; line coverage 58.23% | Local browser files run serially, with the complete suite retained |
| Docs browser suite | 15 passed | `pnpm exec vitest run --browser.headless src/features/docs` |
| Changed docs in Chrome | Three pages verified | AI providers; Task management; multi-statement/Python Tasks. New role/recovery wording rendered; recovery section visually inspected |
| Real Redis role concurrency | Passed | 100 concurrent switches had unique revisions 2–101; deleted sessions were not recreated |
| Real StarRocks uncertain Task | Three attempts passed | Fixture-owned task/node stayed abandoned/running without an executor call; only its own rows were removed |
| PG fixture preparation | Passed | Generated TLS/config permissions 0700/0600; `docker compose config --quiet`; no extra container started |
| Live Console chat and saved history | Six of six attempts passed | Three independent calls each on cx/gpt-6-luna and cx/gpt-6.1-sol; structured synthetic output and persisted history verified; fixtures deleted |

The API auth inventory retains real FastAPI dependencies and replaces application
handlers with a failing sentinel, so a broken auth gate cannot mutate development
data. Token renewal is an explicit exception: its handler validates expired
signed tokens; invalid JWTs are additionally forbidden from reaching Redis.
It proves missing/invalid session refusal, not cross-user authorization or live
endpoint behavior. Internal service-token endpoints remain separately tested.

The PG checks cover actor/role RLS isolation, transaction-local settings, CAS
contention, rollback, schema/epoch refusal, unsafe DB privileges, statement
timeout, terminated connection without retry, diagnostic redaction, read-only
transactions, stale process fencing and a write freeze that waits for existing
transactions. They do not prove completed migrations, PgBouncer, HA, backups,
durable jobs or domain behavior on PG.

## Failures retained and follow-up

The first complete local run in this phase had 9,179 passes and three failures:
old provider/search-key fixtures assigned admin without activating that role.
Fixtures now activate the intended role, and negative inactive-admin cases were
added. Superset and Notebook fake Redis fixtures were also updated to execute
the actual atomic Lua path; assertions were retained.

The expanded full suite exposed an HTTP metric-label collision: FastAPI's
original included route lost its prefix. Metrics now resolve the effective
route context, with nested-prefix and alias regressions. The declared FastAPI
minimum matches the already locked 0.137.2 version whose routing API is used;
no FastAPI package version changed in the lockfile.

The first broader trajectory run had 340 passes and seven failures. Those
trajectories expected one SQL call despite the current semantic tool making one
answer query plus two bounded data-window probes. Updated assertions require
exactly those three calls, one ASC and one DESC probe, one-row probe limits and
the same principal/role/security version on every call. Existing consent,
model-call, provenance and answer assertions remain. The complete rerun passed
347 cases; this is controlled evidence, not live AI acceptance.

Two complete frontend attempts each had 1,592 passes and one failure: the mobile
drawer position was sampled during animation, then a lazily loaded News chart
had not rendered within the default polling deadline. The tests now await the
drawer's animations and give chart loading a bounded 10-second deadline while
retaining the geometry/chart-count assertions. The final complete serial run
passed. These failed attempts remain part of the evidence.

The existing FE container exited with code 255 at 09:08:33 UTC during local
checks; Docker reported OOMKilled=false. FE logs at 16:08:13–16:08:23 local time
record Berkeley DB DiskLimitException with 4.26–4.78 GB free against its 5.37 GB
minimum, immediately before the exit. A live inventory attempt failed while
it was unavailable. The same container and
volumes were started again, and existing host watchers reloaded after their
failed startup. Engine health and the API recovered. This unplanned outage is
not counted as a passed fault-injection or no-duplicate-effect test.

The previous docs commit's CI run `42977447467` completed with 240 integration
cases: 214 passed, two failed, 24 skipped. Both failures expected uncertain Task
nodes to remain abandoned; the worker had instead settled them as failed. The
worker now holds these runs, including when a sibling failed or a finalizer is
uncertain. Unit and local synthetic engine evidence pass. Checkpoint CI run `42996593585` subsequently passed both regressions.
The earlier failing CI attempt remains recorded here.

Those 24 skips include live/custom tools, patched-FE/Ranger acceptance, engine
capabilities, cross-cluster migration, delayed-publication/label-expiry faults,
shared-data mode, governed Superset and observability. The ordinary engine lane
does not certify those capabilities. Required cases need appropriate disposable
CI fixtures; skips cannot be converted into a release PASS.

Live inventory confirms a configured chat provider with two LLMs and a configured
external-search key. Embedding tests were explicitly waived by the owner; no
embedding provider was configured for QA. Live chat, tools, MCP, AI SQL, Studio
and Dataviz acceptance after PG cutover remain pending. The separate
`LIVE_CHAT_CHECKPOINT.json` proves the six pre-cutover Console chat/history
attempts only. It does not certify tool calling, external search, Studio,
Dataviz, dependency failures or post-migration behavior.

## Follow-up: uncertain native submission and complete connection discovery

This follow-up starts from checkpoint commit
`ee4ff8f498aad14d09c9eb2c8021089347a55ba4`. Its application source digest is
`39d8b7c5000eb1ab2e9b6a6e916c0112d75d93fb94a44e2fdfde0c0b25bbcacd`.
The earlier live chat and PG evidence above remains tied to its original code.

Native Task transport loss during submission, response or connection cleanup
now leaves an abandoned node and a held graph. The same applies to an observation
deadline, heartbeat loss or an unrecognized native state. Explicit pre-admission
syntax/access refusals remain known failures. This is a conservative recovery
hold, not the planned PG receipt/reconciliation protocol.

- Ten new unit cases cover seven uncertain paths, two explicit refusals and a
  refusal-shaped error after acknowledgement. Each uncertain-path case tries
  three deliveries and checks one submission, no native deletion, no child or
  finalizer execution, no replacement admission and sanitized stored errors.
- The expanded Task-focused suite passed 151 cases. The complete application
  suite passed 10,425 cases with 65% branch/line combined coverage before the
  scanner additions. Two subsequent 10,427-case runs passed. After the alias authorization
  fix, the final full suite passed 10,452 cases with 65% coverage in 453.09 seconds.
- Full application mypy and the agent scorecard passed (54/54 scenarios,
  189/189 checks). Changed-file Ruff passed after fixing one overlong profile
  description. The two changed Task docs passed the 15-case docs suite and were
  inspected as rendered pages in Chrome; no executable example changed.
- The new real-engine regression injects lost completion after a real INSERT
  commits, then requires three worker deliveries to leave exactly one row and
  one native submission. It collected one case. Execution is pending in CI;
  its seeding/fault setup is not run on the shared development database.

The source review expanded to 875 calls: the original 791 pooled/indirect paths
plus 84 direct connection/cursor candidates. Eight scanner regressions pass,
including raw connection bypasses, mixed cursor functions, dynamic SQL and
review/waiver preservation. All 875 have destination classifications. Table
transforms, per-role grants, constraints and timestamp provenance remain open;
this does not close WP00.

The read-only constraint profile now executes 44 checks on the existing engine.
It found two duplicate workspace revision groups and 135 orphan Task node rows
(128 success, seven failed). The other checked candidates had zero observed
violations. No row, object, password or shared volume was changed by profiling.
These observations are not a frozen migration snapshot.

CI run `42996593585` tests checkpoint `ee4ff8f4`, not this follow-up. Grammar,
backend static/unit, Notebook image/kernel, Superset image/unit, PG17 boundary
and frontend jobs passed. Real-engine integration also passed: 216 passed,
zero failed, 24 skipped. The follow-up's new external-commit regression has not
yet been delivered to CI. The 24 skipped capabilities remain acceptance gaps.

## Follow-up: AI alias authorization and live SQL evidence

Global AI alias creation, update, deletion and UDF registration now require an
active administrator role. Merely having an assigned administrator role is
insufficient. Authenticated users retain read access to alias and registration
metadata. Twenty-five route tests cover the four mutations with each supported
admin role, an inactive admin and a missing role, plus non-admin reads. The live
fixture setup/cleanup exercises the authorized admin path. A live non-admin
mutation test remains pending; controlled tests do not substitute for it.

The final application mypy check passed, and the scorecard passed 54/54 scenarios
and 189/189 checks. The docs browser suite passed 15 cases; the AI provider page
was verified in Chrome with the active-role requirement rendered.

`LIVE_AI_SQL_CHECKPOINT.json` retains the first 21 SQL output validations, three
per text function. Gateway counters showed only seven provider calls, with the
14 repeated results coming from the engine cache. These are SQL output passes,
not 21 independent provider passes. `LIVE_AI_SQL_UNCACHED_CHECKPOINT.json` retains
a second attempt: all 21 failed its explicit provider-invocation criterion,
although outputs were valid. Fresh disposable alias IDs did not bypass caching.

The pinned StarRocks cache key contains the prompt, model, sampling parameters
and timeout, but omits endpoint and alias credential. See the exact
[upstream implementation](https://github.com/StarRocks/starrocks/blob/4a9848edf03f5c936dac664b2d52527f48e72eb0/be/src/util/llm_cache.cpp#L24-L34).
This confirms why alias changes alone could not establish a new provider call;
provider/alias cache partitioning is now tracked as risk R27, not treated as
verified security. All disposable aliases from both attempts were deleted and
the original registration restored. No embedding provider was configured.

`LIVE_AI_SQL_PROVIDER_CHECKPOINT.json` records the third experiment: all 21
independent provider invocations passed, three each for Complete, Sentiment,
Classify, Summarize, Extract, Translate and Filter on the existing
`cx/gpt-6-luna` model. Each attempt required a gateway success counter increase
of exactly one, a successful SQL result and the expected structured/semantic
output. The synthetic SQL input and model parameters were identical within
each function; only the engine observation timeout varied (120–170 seconds),
which is not forwarded to the provider. All calls completed within four seconds.
All 21 temporary aliases were removed and original UDF registration restored.
This verifies pre-cutover happy paths only, not cache isolation, provider
failures, authorization, AI quality on arbitrary inputs or post-PG behavior.

Final changed-file Ruff passed for all 34 Python files in the branch.

## Checkpoint 5c81c9fe: completed CI

[Run 43004216101](https://github.com/dwickyfp/nova/actions/runs/43004216101)
passed all eight jobs for `5c81c9fefc3e898c8206a2ee4d7290fd511dd474`.
The downloaded integration report contains 217 passes, no failures and 24 skips.
`test_lost_completion_after_commit_never_repeats_the_insert` passed against the
real engine: the committed effect was not replayed on redelivery. The 24 skips
remain acceptance gaps, including governed Ranger/Superset scenarios. This run
does not validate the subsequent MCP changes below.

## MCP and Studio follow-up

`LIVE_SQL_TOOL_CHECKPOINT.json` records six successful Console tool trajectories:
three each on `cx/gpt-6-luna` and `cx/gpt-6.1-sol`. Each approved exactly one
`SELECT 6 * 7 AS total`, returned 42 and preserved the answer in history.
All temporary conversations were removed.

Studio web search retained its failed setup experiments in
`LIVE_STUDIO_SEARCH_CHECKPOINT.json` and
`LIVE_STUDIO_SEARCH_DIAGNOSTIC.json`: the fixture lacked a verified active-role
access grant. After using the ordinary grant/verification flow,
`LIVE_STUDIO_SEARCH_VERIFIED_CHECKPOINT.json` recorded three Luna passes, two Sol
passes and one Sol timeout before any search call. These checks validate one
completed search tool, a public documentation URL and matching saved history;
they do not establish that the final URL exactly matched the returned citations.
Revoking each fixture's access returned 404 when starting another conversation.
The agents and conversations were removed. The failed run remains a failure.

A disposable HTTP MCP server exposes only bounded integer multiplication. It
has no Nova credentials or access to business data. The first temporary tunnel
failed to connect; a second HTTP/2 tunnel worked. Discovery then reproduced a
product defect: one tool was discovered but zero tools appeared in the normal
selection API. `LIVE_MCP_DISCOVERY_CHECKPOINT.json` retains that failure.

The registry now includes platform MCP tools alongside the caller's personal
tools. Shared connector mutations, tool enable/disable and endpoint visibility
use the active ACCOUNTADMIN role. Shared tool changes are audited. The browser
displayed `fixture_total` and the connector's Connected state; the matching
docs page was rendered and inspected. The UI also distinguishes a failed load
from an empty registry and provides Retry.

`LIVE_MCP_CHECKPOINT.json` retains a second defect: an immediate approval
returned 404 because the pending card preceded broker registration. The
synthetic server received no call. The experiment was cancelled and its
fixtures removed. Console and Studio now register the pending decision before
publishing it. Closing the generator cancels the waiter, and broker cleanup
checks the future identity so a completed call cannot clear a newer call
that reused its ID. This remains process-local consent, not WP10 durable consent.

`LIVE_MCP_FIXED_CHECKPOINT.json` records five successful external calls and one
stalled Sol run. Each success required zero calls before approval, exactly one
external invocation with arguments 6 and 7, output 42 and matching saved history.
The sixth run emitted an error before calling a tool, but did not finish until
explicit cancellation. `LIVE_MCP_CANCELLATION_DIAGNOSTIC.json` records the
successful cancellation request. Its 257-second duration is not a boundedness
pass. All fixture agents, threads and connector rows were deleted. A final
application-tree run is recorded separately in `LIVE_MCP_FINAL_CHECKPOINT.json`:
all six attempts passed on tree
`42f1c0928163bd4e49a77b792b204bfcc1b92f749246782f2945e5a0e345dd62`.
Each used one external call, no calls before approval, correct arguments and
output, and matching saved history. All agents, threads and connector rows were
removed; the fixture server and public tunnel were stopped. This verifies the
final happy path, but does not explain or erase the earlier stalled run (R30).

Local frontend validation passed 1,598 tests in 193 files, lint with zero errors
and 32 warnings, and the production build. The focused registry/configuration/
docs browser run passed 29 cases. The first focused browser attempt failed
because teardown restored network access while a test view remained mounted;
explicit unmounting now precedes mock restoration. Full backend coverage passed
10,482 tests with four warnings in 597.49 seconds and 66% combined coverage;
new CI delivery is still pending for this follow-up. Full-application mypy
and changed-file Ruff (42 Python files) passed. The eval/benchmark command passed
360 cases and skipped one real-engine benchmark: its guard requires a disposable
test stack owned by the backend directory, while the running project is the
shared development stack. No benchmark provisioning or reseeding was attempted.
The scorecard passed 54/54 scenarios and 189/189 checks.

## Atomic PostgreSQL schema installer

The follow-up application tree is
`e3bcb3f5604f20c68c98e8beb3f5e9c67abfcee416c7ab4767dc8954ae546759`.
The existing PG17 TLS fixture passed 31 tests, with no skips, for immutable
migration checksums, serialized DDL, rollback/crash recovery, protected grants,
actor transactions and typed values. The test schema and newly created roles
were removed after verifying ownership. No development domain was migrated.

The installer creates two deployment tables and leaves the epoch NULL. It has
no activation switch or API startup hook. Full local coverage passed 10,495 tests
with four warnings in 521.74 seconds (65% combined coverage). Full-application
mypy, changed-file Ruff (47 Python files), and the 54-scenario/189-check scorecard
passed. New CI is pending; the earlier interrupted coverage run is not a pass.
Details and the
sanitized testcase report are in
[the WP-02 packet](../../reports/production-refactor/WP-02/README.md).

## Subsequent CI and bounded Studio persistence

CI run [43012324623](https://github.com/dwickyfp/nova/actions/runs/43012324623)
completed successfully for `e1cb5f928a8f727a3fab8dcdc0f1ef698287f718`, including
all eight jobs. Its downloaded real-engine report contains 217 passes, zero
failures and 24 skips. This verifies the MCP/consent checkpoint above; the same
governed/live-provider skip limitations remain. It does not certify later edits.

In the original 257-second stalled MCP run's request window, local logs show
Mission projection calling `assistant_repository.get_thread`, and StarRocks
reporting planner error 1064 after 10,804 ms. This attribution uses the adjacent
fixture request and cancellation; the exception log lacks a run ID. The old producer logged that
exception without emitting a terminal interruption, and cleanup had no deadline.
The exact remaining wait is not established. Sanitized identifiers and the call
path are retained in [RUN_STORAGE_QA.json](RUN_STORAGE_QA.json), without raw logs.

The current route bounds journal/projection and reply writes, closes the shared
generator on failure, stops before another tool, emits an interruption even
when storage remains unavailable, and bounds final cleanup. The browser retains
a partial answer beside the warning, removes expired approval controls and
marks an unfinished running step as **Result not confirmed**. Seven controlled
fault cases and the 58-case focused browser run pass. The user docs were rendered
and visually inspected at `/docs/studio/ask-questions`; no executable example
changed. This is not durable consent, an atomic legacy journal or WP08 acceptance.

New live evidence is retained separately in
[the MCP report](LIVE_MCP_STORAGE_FIX_CHECKPOINT.json) and
[the web-search report](LIVE_STUDIO_SEARCH_STORAGE_FIX_CHECKPOINT.json).
MCP passed five of six attempts; search passed four of six. All attempts reached
a terminal outcome and all disposable resources were removed. Initial attempts
overlapped backend and browser coverage, which saturated the local device;
browser coverage was deliberately interrupted and is not a pass. MCP's first
attempt failed before a tool call, and search recorded one storage interruption
and one turn-budget timeout. Later success does not erase those failures or
close R30. There is no medium-PoC capacity claim.

CI run [43017536132](https://github.com/dwickyfp/nova/actions/runs/43017536132)
subsequently passed all eight jobs on schema-installer commit
`1e0f0626da1d732095030f05f99058aebceee7ae`. Downloaded artifacts confirm 31 PG
passes without skips and 217 engine passes with 24 skips, zero failures/errors.
The original pending-CI statement above is superseded for that commit only.

For the Studio persistence follow-up, full local backend coverage passed 10,502
tests with four warnings in 877.27 seconds (65% combined coverage). Mypy and Ruff
for 48 changed Python files passed. Agent eval passed 54/54 scenarios and 189/189
checks; all 349 controlled trajectories and 11 assistant microbenchmark smoke
checks passed. The latter do not measure provider or engine latency. Final
frontend coverage passed 1,601 tests across 193 files in 374.71 seconds, with
58.4% line coverage. Lint passed with zero errors and 32 warnings, and the
production build passed. The earlier interrupted browser run is retained as
an interrupted attempt. Commands and results are in `RUN_STORAGE_QA.json`;
CI for this follow-up remains pending.

[Wave K](WAVE_K_REVIEW.md) resolves five declared key mappings for tables absent
from the inspected live schema. Eight target-key mappings, per-role grants and
timestamp provenance remain unresolved; no source table was created or altered.


## Restricted PostgreSQL identity provisioning

The next application tree is
`087baab4ad673b7f832bee566d8ca5e560a3c405fbba98636571a22ff05beb4c`, based on
parent commit `d1c8b45d0c9c26831f632b52c65c1edb1ee2b51c`.
The separate bootstrap command atomically provisions 12 restricted workload
logins, migrator/readonly/backup logins and two NOLOGIN roles. Reapplication
verifies deployment markers, exact grants/memberships, safe role settings and
all supplied passwords; it does not rotate passwords or repair unknown state.
Only the backup role receives replication, and only the NOLOGIN owner receives
database CREATE. The command neither installs domain tables nor publishes an epoch.

The existing disposable PG17 TLS fixture passed 59 combined tests with no skips,
including 28 provisioning cases. All 12 workload logins were denied DDL,
unauthorized role switching and deployment-state updates. Concurrent setup has
one creator; controlled rollback and connection termination leave no partial
roles. Final cleanup found zero new roles and test schemas. The first run's
wrong-password classification failure was fixed and retained in the report.

Full local backend coverage passed 10,515 tests with four warnings in 552.28
seconds (65% combined coverage). Mypy, Ruff on 51 changed Python files and the
54-scenario/189-check scorecard passed. Frontend code is unchanged from the
validated Studio checkpoint. Provisioning is added to the non-skipping CI PG
lane, but CI for this source is pending. The
[bootstrap command record](../../reports/production-refactor/WP-02/bootstrap-commands.json)
and [operator contract](PG_BOOTSTRAP.md) preserve exact scope and limitations.


## Studio checkpoint CI and lossless import preparation

CI run [43023889831](https://github.com/dwickyfp/nova/actions/runs/43023889831)
completed all eight jobs successfully for
`d1c8b45d0c9c26831f632b52c65c1edb1ee2b51c`. Downloaded reports contain 31 PG
passes without skips and 217 engine passes with 24 skips, no failures/errors.
This supersedes that checkpoint's pending-CI statement. Live Studio/MCP failures
remain open; neither the skips nor provider evidence changed.

Subsequent import preparation adds strict value conversion and immutable-export
row identities. All 1,578 observed columns have a type mapping; all 249 DATETIME
columns still require provenance. Seven duplicate-key tables preserve every
legacy ID and row occurrence, and the former dashboard key is established from
historical source. No target key definition remains unknown, but complete
constraints, per-domain access, actual schemas and import validation remain open.
The current combined PG suite passes 61 cases without skips; details and exact
scope are in the [WP-03 packet](../../reports/production-refactor/WP-03/README.md).

The final local import checkpoint passed 10,601 backend tests with four warnings
in 457.59 seconds (65% combined coverage). Mypy, Ruff on 53 changed Python files
and the 54-scenario/189-check scorecard passed. The final 61-case PG suite has no
skips, and cleanup left zero new fixture roles/schema after excluding the
pre-existing administrator. CI for this source remains pending.


## Transaction pooling and bounded startup

The next application tree,
`7f432f8901b93d64ef02933df7811947e09568ef80fd2b5b8465c8429d4dc65b`, adds a
validated PgBouncer 1.26.1 path on the existing PG17 fixture. The final combined
suite passed 68 tests with zero skips in 19.24 seconds. Two application pools
shared one backend across 12 simultaneous transaction requests without actor,
role or security-version leakage. Cancellation rolled back the pending write,
and plaintext, wrong-hostname and wrong-password connections were refused.

A locked-ledger startup initially exceeded its deadline because the pooler
ignored connection-startup timeout options. The fix bounds the full validation
phase, closes failed readiness and returns a redacted metadata timeout; recovery
through a new pool also passed. Earlier fixture hostname and native dependency
build failures are retained in the
[pooling report](../../reports/production-refactor/WP-02/pooling-commands.json).
Full backend coverage passed 10,601 tests with four warnings in 468.15 seconds
(65% combined coverage), along with mypy, Ruff on 55 changed Python files and
the 54-scenario/189-check scorecard. CI for these edits is pending. These tests
do not install the production pooler/Compose, port a domain or perform cutover.


## Preference storage and CI infrastructure failure

CI [43029919010](https://github.com/dwickyfp/nova/actions/runs/43029919010)
passed all eight jobs for `a7894247db84c9a9627b5c9a390a4c6636ef0d9a`.
Downloaded reports contain 59 PG passes with no skips and 217 engine passes
with 24 skips, no failures/errors. This supersedes the provisioning checkpoint's
pending-CI statement; it does not prove the later import/pooling changes.

The next run, [43036382504](https://github.com/dwickyfp/nova/actions/runs/43036382504),
failed to install OS dependencies for both PgBouncer and Playwright because the
Ubuntu mirrors could not be resolved. Those jobs failed before their tests ran.
They remain failed evidence, not passes or application-test results. The pooler
build step now bounds package acquisition and refuses a failed index refresh;
it still requires all dependencies and the pinned source checksum.

Revision 0002 and the preference repository implement composite identity,
forced RLS by row category/workload, atomic revision CAS, retained deletion
revisions and encrypted search-key validation. The 94-case local PG suite passes
without skips, including 25 preference cases and upgrade from revision 1.
The [storage contract](PG_PREFERENCES.md) lists the remaining actor/revision
plumbing, audit integration, import and cutover work. No production domain has
switched authority. Unknown historical keys are preserved but inaccessible until
reviewed; the read-only source probe observed 17 rows in ten known key names.

The final preference checkpoint also passed 10,622 backend tests with four
warnings in 480.80 seconds (65% combined coverage), mypy, Ruff on 58 changed
Python files and the 54-scenario/189-check scorecard. Final PG acceptance after
the last DDL edit passed 94 cases with zero skips in 38.36 seconds. Cleanup
left zero test schemas/new roles. Exact evidence is in the
[preference command record](../../reports/production-refactor/WP-03/preferences-commands.json).

The subsequent transaction-composition review moves repository work into the
service's actor-bound transaction and adds a savepoint per batch. An outer
rollback or an internally caught conflict cannot commit a partial batch. The
final application tree `ec36d34ee8c530b13f3904a24052115e2ae69cd81283539826d73f78c7e6a00c`
passed 10,622 backend tests with four warnings in 561.75 seconds (65% combined
coverage), mypy, changed-file Ruff and the agent scorecard. The final combined
PG suite passed 96 cases without skips in 67.65 seconds, including 27 preference
cases. A 90-minute scan of 2,368 fixture log lines found zero occurrences of
the preference secret canaries or encrypted-envelope marker; no raw logs were
exported. Earlier runs remain in the report. CI for this source is still pending.

CI 43036382504 subsequently completed with six successful jobs and two failed
dependency-installation jobs. Its downloaded engine report has 217 passes and
24 skips, no failures/errors. The run remains FAIL because PG and frontend
acceptance never started. The preference checkpoint was pushed separately as
`fe44caa5b4883a32c1637cecdf4be2bfe1b9714c`; its CI evidence is pending.

Revision 0003 corrects the preference password-policy grants after two regression
tests exposed a missing administrator write grant for query and an overly broad
self-clear exception for proxy. Both SQL workloads now require an active
administrator role; the web authentication path retains self-clear after a
verified password change. News administration and personal writes stay with web.
The combined local suite passes 99 PG tests without skips, including a revision-2
upgrade that preserves existing row contents and revision. See the
[password-policy evidence](../../reports/production-refactor/WP-03/password-flags-commands.json).
