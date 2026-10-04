# Studio lifecycle hardening, 2026-10-05

This follow-up starts at `0cdde1987aa81ce823c0e18b72230b7208e82ac3`, the
merged PR #167 foundation, on `feat/studio-lifecycle-hardening` in the managed
worktree. The original checkout and its unrelated navigation changes were
preserved. DevMesh is configured, but no DevMesh session tools were exposed;
the approved hardening brief supplied the goal and acceptance criteria.

## Findings and implementation

| Finding | Root cause | Change and owner |
| --- | --- | --- |
| Runtime timezone | Loop construction omitted the effective database timezone, and business/UI paths supplied Jakarta defaults. | `configured_timezone()` supplies explicit Direct, Smart, Nove, evaluation, and scheduled-turn context. Objects retain explicit timezone precedence; retry restores pinned time context. New omitted-timezone inputs resolve configuration, while update/retry preserves stored timezone before digest checks. |
| Same-turn Investigation facts | The business hook emitted public activity after execution without enriching the result consumed by the provider. | `BusinessResultHookResult` separates public event, provider observation, and trace metadata. AssistantLoop enriches the tool envelope before the next provider iteration. Exact Investigation/News/comparison revisions supply facts and hypotheses. |
| Answer verification | Hypothesis contributions were absent from the numeric evidence tables. | Canonical comparison and hypothesis tables enter the existing EvidenceTracker and final verifier. Structured hypothesis claims cannot replace canonical causal labels. Wrong contribution 999 and arithmetic-to-supported-effect claims are rejected in final output. |
| Continue/New routing | Only New reset after acceptance; Continue remained sticky. | Send captures the selected routing choice. Both choices reset on acceptance, preserving a newer choice if it changed during the request. Pre-acceptance failure retains draft/routing; accepted reconnect uses the run journal. |
| Evidence and Context | Tool-call identity lacked run provenance, and rail evidence followed conversation history rather than the selected Mission. | Server-owned workflow provenance survives SSE, trace, checkpoint, Smart evidence import, and replay. Run/tool-call pairs deduplicate transport evidence. The shared Mission selector scopes Evidence and Context; All conversation evidence exposes cross-Mission/legacy evidence explicitly. Exact identity/metric and stale-response guards protect Context selection. |
| Health and prefetch | Numeric legacy confidence entered provider data, while the workflow flag serialized every independent read. | Numeric semantic confidence is trace/debug compatibility only, including historical provider reconstruction. Mission-bound work and active ordered hooks stay serial; independent reads retain consent/dependency/release/budget checks and overlap. Automatic Investigation requires current effective INVESTIGATE intent and a validated seed. |
| Canonicalization cost | Internal analytical work was invisible in tool-selection accounting and trace cost. | Existing Intelligence budgets share a bounded collector for actual dispatches, cache reuse, queries, persistence, and created/reused/incomplete/skipped/failed status. Semantic tool time is measured separately. Trace/Quality preserve these facts; Quality shows the recorded breakdown without treating replay as execution. |
| Public API | Durable contracts carried scopes, bindings, operation digests, projection cursors, leases, and fences into Studio transport. | Typed nested allowlists project Mission, Investigation, Action, and related canonical objects on the same HTTP endpoints, SSE, and legacy replay. Existing pagination tokens and journal sequence/event IDs retain their access/replay contracts. The internal persistence/admission/proof contracts remain unchanged. Projection follows authorization and never grants access. |
| Scenario discovery | Context-free catalog data could not establish compatibility, published currency/unit, or exact dependencies. | Investigation revision and optional Mission context reauthorize dependencies. Discovery and Decision use the same adapter compatibility rules. Definitions include reason codes, required inputs, and resolved target/currency/unit. Semantic currency is read-only; permitted manual input and conflict refusal retain registry ownership. Unknown currency is not filled with IDR and no FX conversion is guessed. |

Provider observations contain at most 16 KiB UTF-8 JSON, ten hypotheses, and
twenty canonical evidence references. Truncation is explicit, hypothesis references
point into the retained list, and unknown counts/alternative decompositions remain
limitations. Scope, credentials, security binding, attachments, and private reasoning
are excluded. Smart imports only authorized completed/idle specialist evidence;
coordination prose is not data evidence.

## Compatibility and migrations

No DDL, dependency, engine patch, deployment secret, or production flag changed.
JSON evolution uses the existing tables and journals. New transport provenance is
separate from semantic metric definitions. The context-free Scenario catalog stays
available; Studio uses contextual discovery and fails closed when compatibility
is absent. Public projections replace internal responses on existing endpoints,
as approved because there are no external consumers of the internal fields.

Historical Monitor/Automation configuration defaults remain on persistence models.
Only new request handling resolves the configured timezone. Omitted-timezone retry
restores the existing configuration before hashing, including under the publication
fence. Historical Decision request matching preserves the old implicit-IDR digest;
new context/execution never uses that default. Existing Decision, approval, Action
event, request, revision, and fence checks remain owned by their original services.

## Locked baseline and failure classification

| Gate | Baseline |
| --- | --- |
| Backend coverage | 6,262 passed; branch-aware total coverage 58% |
| Deterministic evals | 288 passed |
| Eval scorecard | 48/48 scenarios; 165/165 checks |
| Frontend lint/build | Passed; 25 nonblocking lint warnings |
| Frontend coverage | 1,085 passed across 139 files |
| Lifecycle browser matrix | 24 passed |
| Fixture benchmarks | 13 passed |
| Relevant seeded stock integration | 10 passed |
| Real stock query benchmark | 1 passed; 20 samples |
| Patched-FE/Ranger acceptance | 5 passed; no skips |

The planning baseline's first 39-test attempt missed an existing prefetch timing
assertion; its rerun passed all 39. This is `PRE_EXISTING_FAILURE`, not a hardening
regression. A later focused run under concurrent worker load missed the same timing
gate; the quiet rerun passed all six prefetch cases. The timing assertion remains
unchanged, and an additional synchronization barrier proves overlap independently.

Baseline frontend coverage exposed a missing capability mock in
`history-pagination.test.tsx`: a real 401 request redirected its test page to
sign-in. A narrow fixture repair retained the existing assertions; the isolated
four cases and full 1,085-test baseline passed. A cold optimizer reload also produced
invalid-hook failures before a warmed repeat. These baseline events are recorded as
`PRE_EXISTING_FAILURE` with fixture/environment repair, not new product regressions.

After contextual discovery changed, eight lifecycle browser cases still returned
catalog-only fixtures. Updating them with explicit compatibility and resolved unit
restored the 24-case matrix, including its original keyboard, loading, layout,
permission, and uncertain-action checks. This was a hardening fixture mismatch,
not evidence that missing compatibility should be accepted by production code.

The first combined coverage/integration runs exposed old assertions that expected
`scope` and execution bindings in public API responses. Those assertions now verify
their exclusion. Persistence-owner reads still assert the original scope, historical
run bindings, binding generation, and current caller; they were not removed.

Final independent review found two additional regressions and both were repaired.
An automation timezone override shared state with the monitor preview, so switching
back to monitor silently changed its explicit calendar. Separate report/monitor
resolution preserves explicit object and configured timezone precedence. Public plan
collections originally rejected parser-supported `null`, breaking detail/list HTTP
responses for valid legacy Monitors. Nullable public collections now preserve the
original plan shape and digest. Browser switch-back and HTTP list/detail regressions
cover these cases.

## Validation record

All commands run in the active worktree, with backend commands in `backend/` and
frontend commands in `frontend/`. Dependency setup used Python 3.11.13,
`uv sync --locked` (119 packages resolved, 113 audited), Node 22.22.3, and pnpm
11.8.0. Frontend setup used `npm exec --yes --package=pnpm@11.8.0 -- pnpm install
--frozen-lockfile` and the same prefix for `pnpm exec playwright install --with-deps
chromium`. Neither lockfile changed.

| Gate | Delivered-code result |
| --- | --- |
| Full backend coverage | 6,537 passed, 58% branch-aware total; baseline 6,262 |
| Full deterministic eval | 299 passed; baseline 288 |
| Eval scorecard | 48/48 scenarios, 165/165 checks; unchanged |
| Blocking changed-file Ruff | Passed on all 47 changed Python files |
| User-data system-access verification | Passed; no system/root shortcuts in user-data adapters |
| Frontend lint/build | Passed on final UI; 24 nonblocking lint warnings (baseline 25) |
| Frontend browser coverage | 1,131 passed across 143 files; baseline 1,085 across 139 |
| Lifecycle browser matrix | 24 passed; post-review Action/combined surface check 31 passed |
| Native/Quality/prefetch fixture benchmarks | 16 passed; baseline 13 |
| Real stock query benchmark | 1 passed, 20 samples, no skips |
| Seeded stock integration | 192 unique passed, 14 capability/opt-in skips; affected seven-case repeat passed |
| Patched-FE/Ranger | 5 passed, no skips, 226.35 seconds; baseline 5 passed |
| Patched-Ranger repeated journey | Baseline 3 passed; final 3 passed, no errors/skips, 401.27 seconds overall |

Exact backend gate commands:

```sh
uv run --locked --no-sync pytest tests/unit --cov=app --cov-branch \
  --cov-report=term-missing \
  --cov-report=xml:/tmp/nova-hardening-unit-final3-coverage.xml \
  --junit-xml=/tmp/nova-hardening-unit-final3.xml
uv run --locked --no-sync pytest tests/eval -q \
  --junit-xml=/tmp/nova-hardening-eval-final.xml
uv run --locked --no-sync python -m tests.eval.report
uv run --locked --no-sync python scripts/check_user_data_system_access.py
NOVA_GOVERNED_PERFORMANCE_REPORT=/tmp/nova-hardening-performance-final.json \
  uv run --locked --no-sync pytest tests/benchmark/test_assistant_loop.py \
  tests/benchmark/test_governed_studio_performance.py \
  tests/benchmark/test_studio_prefetch_performance.py -q -s
```

Ruff paths were obtained from both `git diff --name-only --diff-filter=ACMR` and
`git ls-files --others --exclude-standard`, restricted to backend Python files.
This includes new untracked implementation/tests before staging.

The exact blocking lint invocation, from the repository root:

```sh
python3 - <<'PY'
import subprocess
paths = set(subprocess.check_output(
    ['git', 'diff', '--name-only', '--diff-filter=ACMR'], text=True).splitlines())
paths.update(subprocess.check_output(
    ['git', 'ls-files', '--others', '--exclude-standard'], text=True).splitlines())
py = sorted(p.removeprefix('backend/') for p in paths
            if p.startswith('backend/') and p.endswith('.py'))
raise SystemExit(subprocess.run(
    ['uv', 'run', '--locked', '--no-sync', 'ruff', 'check', '--force-exclude', *py],
    cwd='backend').returncode)
PY
```

Frontend commands use the pinned pnpm prefix above:

```sh
pnpm lint
pnpm build
pnpm test:coverage
pnpm test src/features/intelligence/action-preview.test.tsx \
  src/features/studio/lifecycle-surfaces.test.tsx
```

Chromium browser coverage and the surface matrix exercise light/dark themes,
320/1280-pixel layouts, keyboard/focus, acceptance failure, accepted reconnect,
Mission/legacy evidence selection, stale Context responses, loading, missing
compatibility, permission denial, and uncertain Action verification. Quality cost
breakdown additionally checks text contrast at least 4.5:1 and no horizontal
overflow in both themes/layouts. Existing Nova tokens/components remain in use;
no new palette, assets, or fonts were introduced.

## Performance comparison

The same archived foundation (`0cdde1987aa81ce823c0e18b72230b7208e82ac3`) and active
backend were loaded through the locked runtime. Reports preserve source hashes.

| Fixture, p50/p95 milliseconds | Before | After |
| --- | --- | --- |
| Native AssistantLoop, 25 samples | 0.837 / 1.241 | 0.800 / 1.263 |
| Pure evidence/Mission projection, 25 samples | 0.250 / 0.287 | 0.253 / 0.285 |
| Quality case result, 25 samples | 0.073 / 0.080 | 0.071 / 0.074 |
| Independent reads, workflow flag on, five samples | 43.720 / 44.713 | 22.264 / 23.669 |
| Independent reads, workflow flag off, five samples | 22.605 / 133.506 | 22.478 / 165.911 |
| Mission-bound reads, flag on, five samples | 43.826 / 45.862 | 44.099 / 46.804 |
| Ordered hook, flag off, five samples | 22.113 / 22.940 | 44.316 / 45.118 |
| Ordered hook, flag on, five samples | 43.991 / 44.362 | 44.634 / 45.977 |
| Real engine `SELECT 1` round trip, 20 samples | 209.910 / 445.773 | 204.149 / 400.422 |
| Patched-Ranger journey call phase, three samples | 130,759.143 / 172,840.971 | 122,349.268 / 125,445.678 |

With workflow enabled, independent read peak concurrency changes from one to two.
Mission and active-hook paths remain serial; the previously unguarded ordered hook
with the flag off is now serial too. A synchronization rendezvous verifies overlap
without relying on timing assertions. Cold initialization dominates the first
flag-off sample's p95; five samples do not establish stable tail latency. Existing
timing gates were not weakened.

Every prefetch turn retains three provider calls (one planner, two action calls),
two selected tools, and zero canonicalization queries. Native fixture counts remain
two provider calls, zero tools, one participant, 50 response tokens, 20 context
tokens, and zero metadata reads. These are fixture counts, not complete live Smart
journey totals or vendor usage. Pure projection adds no provider calls.

The same automatic-Investigation fixture dispatches four semantic plans at creation
and six during authorized reuse before and after. Creation produces five canonical
objects; reuse produces zero. New telemetry classifies creation as two comparison
and two driver queries, with two cache reuses; reuse has six authorization queries,
no new comparison/driver dispatch, and no persistence. Parent-hook telemetry includes
the additional Mission linkage authorization work separately. Replay preserves the
recorded attempt costs instead of recounting queries.

Exact prefetch measurement command, repeated with foundation/active backend roots:

```sh
uv run --locked --no-sync python tests/benchmark/studio_prefetch_report.py \
  --backend-root /tmp/nova-hardening-baseline-source/backend --samples 5 \
  --source-revision 0cdde1987aa81ce823c0e18b72230b7208e82ac3 \
  --output /tmp/nova-hardening-prefetch-baseline-final.json
uv run --locked --no-sync python tests/benchmark/studio_prefetch_report.py \
  --backend-root "$PWD" --samples 5 \
  --source-revision feat/studio-lifecycle-hardening-working-tree \
  --output /tmp/nova-hardening-prefetch-after-final.json
```

Fixture overhead and real SQL/Ranger I/O are reported separately. No live inference
vendor latency or causal business improvement is claimed.

The repeated journey excludes collection and fixture setup/teardown from the
timings; its small-sample p95 is the maximum. Complete journey provider/tool/token/
participant counts are explicitly unavailable. Before/after counts, distributions,
structural probes, and source hashes are retained in
[the bounded measurement artifact](studio-lifecycle-hardening-20261005-metrics.json).
Personal source paths were replaced with foundation/active labels; no credentials
or application evidence rows are included.

## Runtime acceptance

The stock stack uses isolated Compose project `nova-hardening-l3`, localhost ports
45930/45830/45379/45900, pinned StarRocks 4.1.4, and a verified FE `-Xmx2048m`.
The disposable Ranger stack uses `nova-lifecycle-ranger`, localhost FE 47930,
patched image `docker-starrocks-fe:task-role-20260926`, and verified FE `-Xmx2g`.
Existing `nova-*` containers, policies, and volumes were not removed or reset.
Only task-owned stacks were stopped to serialize engine memory use. Completed
task-owned stock containers were later removed, without removing volumes, to
restore Docker disk headroom.

Stock commands, from the repository root unless marked backend:

```sh
docker compose --project-name nova-hardening-l3 \
  --env-file /tmp/nova-lifecycle-stock-acceptance.env \
  -f backend/docker-compose.test.yml up -d --wait
# backend/
COMPOSE_PROJECT_NAME=nova-hardening-l3 \
  uv run --env-file /tmp/nova-lifecycle-stock-acceptance.env \
  bash tests/integration/seed_engine.sh
COMPOSE_PROJECT_NAME=nova-hardening-l3 \
  uv run --locked --no-sync --env-file /tmp/nova-lifecycle-stock-acceptance.env \
  pytest tests/integration -m engine -v -rs --log-cli-level=WARNING \
  --junit-xml=/tmp/nova-hardening-stock-delivery.xml
COMPOSE_PROJECT_NAME=nova-hardening-l3 \
  uv run --locked --no-sync --env-file /tmp/nova-lifecycle-stock-acceptance.env \
  pytest tests/integration/test_intelligence_studio_live.py \
  tests/integration/test_studio_business_metadata.py -v -rs --tb=short \
  --junit-xml=/tmp/nova-hardening-stock-projection-rerun.xml
NOVA_ORCH_SR_HOST=127.0.0.1 NOVA_ORCH_SR_PORT=45930 \
  uv run --locked --no-sync --env-file /tmp/nova-lifecycle-stock-acceptance.env \
  pytest tests/benchmark/test_assistant_engine.py -q -s -rs
```

The full stock selection initially returned 189 passed, three stale public-field
assertion failures, and 14 skips (30 cases are outside the engine marker).
All seven affected cases passed after fixture correction, including the three
previous failures: 192 unique executed cases passed. The complete multi-story
bootstrap/review test continued well beyond its former early scope assertion;
the seven-case run took 1,090.46 seconds including fixture work. This duration is
not a Studio-turn latency measurement.

The stock skips are explicit capability/environment gates: custom SQL live and
live-provider probes (two), patched-FE/Ranger surfaces (seven, with governed
acceptance run separately), disabled UDF and unsupported native CREATE TASK (two),
cross-cluster migration (one), and shared-data topology (two). None of the required
governed Ranger cases may be skipped for final acceptance.

Disposable Ranger startup restored dependencies/bootstrap before FE/BE. Docker
health's `SELECT 1` did not establish metadata readiness: the restarted BE was
still automatically blacklisted. An early attempt returned five setup errors.
Only this disposable BE's automatic blacklist entry was removed with
`DELETE BACKEND BLACKLIST 10001`; a real `NOVA_SYSTEM.CONFIG_AGENTS` read then
succeeded. See the [StarRocks command reference](https://docs.starrocks.io/docs/sql-reference/sql-statements/cluster-management/nodes_processes/DELETE_BACKEND_BLACKLIST/).
The next run exposed one old dictionary-hook fixture; its typed channels and
effective INVESTIGATE intent were updated while preserving historical/access
assertions. These failed attempts are retained as environment/contract-fixture
failures, not represented as passing acceptance.

The first post-change journey measurement lost the FE during sample one and
returned one failure plus three setup/cleanup errors, with zero valid timings.
The FE log established `com.sleepycat.je.DiskLimitException`: free disk was
5,329,268,736 bytes, below BDB's unchanged 5,368,709,120-byte reserve. Docker
reported `OOMKilled=false`. Removing only the completed task-owned stock
containers reclaimed their writable storage; the disposable FE restarted with
the same 2 GiB heap. A unique readiness database create/drop and metadata read
verified journal writes before the complete three-sample retry. No reserve,
assertion, production flag, engine image, or shared Nova resource was weakened.
That retry passed its first sample, then the FE journal again refused writes:
disk free space was 3,159,875,584 bytes. It returned one passed, two failed, and
one cleanup error, so its single timing is not used as a valid benchmark.
Subsequent filesystem probes reported 7.9 GiB available; journal write/read
readiness passed again before another full three-sample run. The precise cause
of the changing filesystem headroom was not established. Recovery/restarts are
timing confounders and cannot establish a performance gain.

The next complete run kept FE alive and passed two samples, but sample two failed
when StarRocks metadata INSERT logical planning took 3,170 ms and exceeded the
engine's existing planner limit. Its report correctly omitted successful aggregate
timings and returned failure (743.27 seconds overall). Another full three-sample
run retained the same heap, planner limit, assertions, and command; partial results
were not combined into a passing benchmark.

The final complete retry passed all three samples in 401.27 seconds, with zero
errors/skips. Call phases were 125,445.678, 121,335.103, and 122,349.268 ms. The
measurement artifact includes only the successful before/after distributions;
the failed-attempt causes above remain part of this report.

Final Ranger commands:

```sh
# repository root
docker compose --env-file /tmp/nova-lifecycle-ranger.env \
  -f /tmp/nova-lifecycle-ranger.yml up -d --wait starrocks-fe starrocks-be
# backend/
uv run --locked --no-sync --env-file /tmp/nova-lifecycle-governed-acceptance.env \
  pytest tests/integration/test_governed_studio_ranger.py -v -rs --tb=short \
  --junit-xml=/tmp/nova-hardening-ranger-final2.xml
uv run --locked --no-sync --env-file /tmp/nova-lifecycle-governed-acceptance.env \
  python -c 'import sys; sys.path.insert(0, "/tmp/nova-hardening-baseline-source/backend"); from tests.benchmark.governed_studio_journey import main; raise SystemExit(main())' \
  --samples 3 --report /tmp/nova-hardening-ranger-journey-baseline.json
uv run --locked --no-sync --env-file /tmp/nova-lifecycle-governed-acceptance.env \
  python -m tests.benchmark.governed_studio_journey --samples 3 \
  --report /tmp/nova-hardening-ranger-journey-after-complete.json
```

Compose interpolation passed independently, without printing secret-bearing
configuration, for `docker/docker-compose-engine.yml`, its development override,
and `backend/docker-compose.test.yml`. No Compose source changed.

```sh
docker compose --env-file /tmp/nova-lifecycle-ranger.env \
  -f docker/docker-compose-engine.yml config --quiet
docker compose --env-file /tmp/nova-lifecycle-ranger.env \
  -f docker/docker-compose-engine.yml -f docker/docker-compose.dev.yml config --quiet
docker compose --env-file /tmp/nova-lifecycle-stock-acceptance.env \
  -f backend/docker-compose.test.yml config --quiet
```

## Rollout boundaries

All production flags remain false. Business Workflow requires controlled acceptance
of same-turn facts, Mission separation/replay/resume, current authorization, and
the shared Evidence/Context selection before enablement. Actions require their own
policy, consent, fencing, configuration-readback, and compensation acceptance;
verified configuration is an operational effect and does not prove business uplift.
Quality requires compatible frozen evaluations and human publication gates before
promotion; cost telemetry does not upgrade usage observations to business truth.

Analytical Workspace remains `BLOCKED_BY_INFRASTRUCTURE` with
`UnavailableAnalysisExecutor`. There is no insecure execution fallback. Scripted
fixture and real-engine acceptance do not establish live model accuracy or vendor
latency.

All applicable local gates above passed before commit/push. These results support
a controlled technical pilot under the stated Business Workflow, Action, and
Quality boundaries; they do not authorize publication or production flag changes.
GitHub Actions is a separate remote check and its state is reported with the PR.
