# Governed Studio operations and acceptance

Use this guide with [Architecture 15](arch-15-governed-studio.md),
[Ranger operations](29-ranger-access-control.md), and the
[CI workflow](../.github/workflows/ci.yml). It describes migration,
rollout controls, recovery, and required acceptance checks.

## Rollout controls

Keep these settings at their shipped defaults until their required acceptance
gates have evidence:

```dotenv
STUDIO_BUSINESS_WORKFLOW_ENABLED=false
STUDIO_ACTIONS_ENABLED=false
STUDIO_QUALITY_ENABLED=false
STUDIO_ANALYSIS_WORKSPACE_ENABLED=false
```

The engine Compose app profile forwards these settings from `docker/.env` to
the API and task worker, with false defaults. `dev.sh` resolves the existing
backend settings once and forwards the same booleans to its API, scheduler,
task worker, and Smart worker. A separately launched Smart worker must receive
the same settings through its process environment or backend `.env`; the Compose
app profile does not create that process. Restart affected processes after
environment changes. Production scoring also needs per-agent opt-in and
an authorized role execution binding. Keep analytical execution disabled while
no isolated executor exists, including after other workflow gates pass.

API, task-worker, and Smart-worker startup logs contain a credential-free Studio
capability snapshot. Studio's Capabilities → Business workflow view reads the
same additive `runtime` contract from `/api/v1/agents/studio/capabilities`.
`enabled` describes the rollout flag; `available` describes whether that feature
can operate with its configured infrastructure. These fields do not promise
caller permission or replace policy and consent checks. Analysis additionally
reports `executor_available`. If its flag is true but no isolated executor is
configured, status is `BLOCKED_BY_INFRASTRUCTURE`, and execution returns HTTP
`503`, an unavailable result with that blocker, and no output. This is the
selected production state until isolated infrastructure passes acceptance.

Use stable signing/encryption keys shared by API/workers, existing managed
storage, and the patched FE/Ranger path for governed data. Start the existing
scheduler/task worker for monitor/quality schedules and the Smart worker for
participant turns. Read [process startup](../HOW_TO_RUN.md#urutan-startup-harian)
and verify ownership; a healthy API does not prove workers are running.

## Additive migration and bootstrap

[20261003_governed_studio.sql](../backend/migrations/20261003_governed_studio.sql)
adds flat tables without replacing existing journals:

| Tables | Purpose |
| --- | --- |
| `CONFIG_AGENT_RELEASE_MANIFESTS` | Immutable dependency manifests |
| `CONFIG_AGENT_QUALITY_CASES`, `CONFIG_AGENT_QUALITY_RUNS`, `CONFIG_AGENT_QUALITY_MONITORING`, `CONFIG_AGENT_IMPROVEMENT_PROPOSALS` | Revisioned quality records/review proposals |
| `CONFIG_STUDIO_MISSIONS`, `CONFIG_STUDIO_DELIVERABLES` | Mission projections and evidence-backed deliverables |
| `CONFIG_STUDIO_RESOURCES`, `CONFIG_STUDIO_RESOURCE_GRANTS` | Attachment references/participant grants |
| `CONFIG_INTELLIGENCE_ACTIONS`, `CONFIG_INTELLIGENCE_ACTION_EVENTS`, `CONFIG_INTELLIGENCE_COMPARISONS` | Action ledger/events and one-off comparison lineage |

Fresh-install [bootstrap SQL](../docker/init-nova.sql) includes the matching
governed Studio table block. Existing installations apply the additive migration
through their operator control-plane connection, then run updated owning
initializers. The SQL file creates new tables; runtime column upgrades are an
additional step.

[20261003_governed_studio_columns.sql](../backend/migrations/20261003_governed_studio_columns.sql)
records nullable upgrades to existing agent, usage, and run tables. Apply only
missing columns after inspecting `DESCRIBE`, or use the owning initializers
below. Fresh bootstrap includes their current table definitions.

The [agent initializer](../backend/app/modules/agents/repository.py) adds nullable
`CONFIG_AGENTS.release_manifest_id` and semantic usage columns `active_role`,
`security_context_version`, and `semantic_version`. The
[assistant initializer](../backend/app/modules/assistant/repository.py) adds
message `security_context` where absent. Mission/resource and Intelligence
initializers create their tables. Preserve existing rows/nulls: legacy agents
remain unevaluated, and unknown-scope historical usage cannot receive guessed
permissions for learning.

Lifecycle closure adds no tables or columns. Mission owner/current/historical
bindings, exact canonical pins, continuation anchors, execution time contexts,
deliverable snapshots, and proposal application references evolve existing JSON
payloads. Legacy Mission fields are synthesized from their original scope on
read. Existing session/security columns remain execution metadata; historical
canonical object scopes and proof digests remain immutable.

For a configured operator environment, run this from `backend/` before enabling
workflow traffic. It uses the existing control-plane connection and initializers
without enabling flags or reading caller business data:

```bash
uv run python - <<'PY'
import asyncio
from pathlib import Path
from app.core.database import db
from app.modules.agents.repository import agent_repository
from app.modules.agents.run_journal import run_journal
from app.modules.assistant.repository import assistant_repository
from app.modules.agents.mission import mission_service
from app.modules.agents.resource_delegation import resource_delegation
from app.modules.intelligence.engine_schema import ensure_engine_schema

async def upgrade():
    await db.init_system_pool()
    try:
        migration = Path("migrations/20261003_governed_studio.sql").read_text()
        for statement in migration.split(";"):
            if statement.strip():
                await db.execute_system(statement)
        await agent_repository.ensure_schema()
        await run_journal.ensure_schema()
        await assistant_repository.ensure_schema()
        await ensure_engine_schema()
        await mission_service.ensure_schema()
        await resource_delegation.ensure_schema()
    finally:
        await db.close_system_pool()

asyncio.run(upgrade())
PY
```

Keep flags disabled if a statement/initializer fails. Startup schema checks are
best-effort and can log warnings while the API starts; startup alone is not
migration evidence. Acceptance must cover fresh initialization, repeat
application, and upgrades from actual legacy tables with existing rows. The seed
script tolerates some bootstrap errors, so its exit status alone does not prove
the full bootstrap succeeded.

## Release promotion and production review

Save a draft using the current agent `config_revision`. Prepare its manifest
through `/api/v1/agents/{agent_id}/versions/{version_id}/manifest`, define scoped
quality cases, and evaluate through the quality runs API. Inspect every score and
frozen case revision. Publish with the current revision and matching
`quality_run_id` when all mandatory critical cases and configured additional
quality/performance gates pass. Runs freeze `gates.other_cases`
(`report_only`, `mandatory`, or `all`), `required_scorers`, `performance`
(`report_only` or `required`), `max_latency_ms`, and `count_budgets`. Inspect their
separate `gate_results`; performance defaults to report-only. An unavailable required
scorer, missing critical case, stale case revision, or dependency drift requires
another evaluation. Restoring a manifested version follows these checks.

A null manifest preserves legacy operation and means unevaluated. Preparing a
manifest subjects that version to promotion checks. With a manifested release
active, direct runtime edits return `409`; use a new draft. External model/MCP
behavior remains mutable even when observable configuration matches. Pins never
preserve revoked permissions or replace live consent.

For evidence assertions, inspect the recorded public health and completeness
fields. Missing inputs yield unavailable comparisons. Do not waive an unavailable
required score to make a release promotable.

Production monitoring defaults to disabled, sample rate `0.1`, maximum `20`
traces, and cadence `60` minutes. It scores persisted traces without replaying
mutations. Review missing evidence and Doctor hypotheses before accepting an
improvement. Accepting a proposal is not runtime publication. Like feedback
continues to suggest verified queries through existing review.

Doctor requires compatible frozen datasets, case revisions, scorer versions,
and gates before naming a known-good and first bad evaluated release. Missing
compatible evidence appears as a requirement. Review a concrete patch and its
base before application. Accepted patches create an agent draft or a Semantic
proposal through their current owners; retry the same operation to recover the
same application. A stale base requires a fresh review. Evaluation, promotion
gates, and human publication still apply.

## Mission continuation, resume, and evidence

Studio keeps chat primary with Activity, Evidence, and Context beside it.
Continue/new controls express intent explicitly. Replay resolves its original
Mission before continuation policy. Successful semantic anchors and canonical
references can continue active work; a different semantic target or population,
unrelated request, or ambiguous objective starts separate work. Cancelled work
cannot continue and completed work needs explicit continuation.

Use the resumable summary and explicit resume operation for a new session.
Resume checks the expected revision and stable operation identity under existing
admission fencing. The current owner and role must still access the thread,
agent, pinned releases, semantic versions, canonical records, and original
uploaded resources. An active old binding or uncertain mutation must be
reconciled first. Resume preserves historical run bindings and approval proofs;
new execution uses current credentials, policy checks, and fresh consent.
Refusals are audited without exposing credential material.

Automatic Investigation follows a successful, complete, validated semantic
execution with safe canonical inputs. It uses the persisted concrete SQL window
and baseline, never a later wall clock. In-progress periods share an elapsed
local calendar span, including month/leap clamping; DST duration differences
remain explicit. An exactly matching authorized Monitor is reused, otherwise a
disabled one-time comparison has unknown sample count. This allows arithmetic
decomposition with reduced statistical confidence. Scheduled Monitors still
need reviewed count semantics.

“Complete investigation setup” appears only for structured missing inputs,
with established semantic identity, windows, and timezone prefilled. Do not
substitute guessed filters or counts. Retry with the same operation identity
after interrupted creation; linkage recovery reuses the canonical comparison
and Investigation. Evidence replay uses the same bounded envelope as live SSE.
Context selection resolves the exact published semantic version and metric.

Deliverables freeze authorized factual source snapshots and exact revisions.
Report generation does not rerun business analysis. Unavailable, changed, or
oversized sources fail with bounded errors. Forecast content requires a pinned
canonical forecast artifact. Learning sources respect current authorization and
opt-out, and remain review proposals; frequent usage cannot confer verified
authority.

## Action and resource recovery

Reload current revisions before reviewer approval, execution, verification, or
compensation. The original dispatch receipt stays immutable; disable readback
is recorded separately as `compensation_receipt`. Decision approval, Action
approval, and per-call allow-once consent
serve separate checks. Smart participants cannot execute business actions. The
monitor schedule requires an authorized role execution binding.

Both `monitor-v1` and `automation-v1` require the active published Semantic View
version for scheduled operations, including verification and compensation.
Mission-authorized historical reads can still display a pinned published version
after replacement. A stale scheduled Action returns `409`; refresh governed
evidence and review a new Decision and Action against the active version. Do not
rewrite historical scope, receipts, or consent to bypass this refusal.

| Observed state | Operator response |
| --- | --- |
| `awaiting_consent` | Resolve through the owning thread's consent flow; recover expired consent through the supervised endpoint. |
| `verification_required` | Inspect the ledger and verify monitor/schedule readback. Do not create a fresh key to bypass uncertain dispatch. |
| `compensation_required` | Reconcile disable/readback state; preserve the recorded compensation attempt and review changed configuration. |
| Changed digest or stale revision | Reload and review the current request; `409` protects operation identity. |
| `cancelled` or `denied` before dispatch | Confirm dispatch attempts remain zero; another operation needs a separate user request/review. |

Successful `monitor-v1` verification means the monitor/task schedule match the
authorized configuration. It does not mean a business intervention occurred or
caused improvement. Compensation disables this setup through another consented
operation. Outcome evaluation still needs a valid observation window, complete
evidence, and existing attribution guards.

`automation-v1` uses the existing Studio automation owner with a stable creation
identity and current authorized binding. Its initial delivery target is Studio.
Creation, configuration readback, and consented disable compensation establish
an operational effect. They carry the same dispatch fencing and uncertain
mutation recovery rules as monitoring; external delivery is excluded.

Resource failures require checking session/role/security version, original
message/digest, participant lineage, and explicit grants. Do not copy bodies into
coordination messages or widen sibling access. Historical source checks are
narrowly authorized by the Mission's original run binding; a new run needs new
grants and current consent.

## Acceptance commands

Run checks against final delivered source/configuration. Full suites remain
required; focused files are diagnostic supplements. These commands are a runbook,
not recorded passing results.

### Backend and deterministic behavior

From `backend/`:

```bash
uv sync --locked
uv run pytest tests/unit --cov=app --cov-branch \
  --cov-report=term-missing --cov-report=xml:coverage.xml
uv run pytest tests/eval
uv run python -m tests.eval.report
uv run python scripts/check_user_data_system_access.py
```

Before commit, run blocking Ruff against changed/new Python paths. This includes
tracked and untracked changes in the shared worktree; full-tree Ruff and mypy
remain report-only in CI:

```bash
uv run python - <<'PY'
import subprocess
tracked = subprocess.check_output(
    ["git", "diff", "--relative", "--name-only", "HEAD", "-z", "--", "*.py"]
).decode().split("\0")
new = subprocess.check_output(
    ["git", "ls-files", "--others", "--exclude-standard", "-z", "--", "*.py"]
).decode().split("\0")
paths = sorted({path for path in tracked + new if path})
if paths:
    subprocess.run(["uv", "run", "ruff", "check", "--force-exclude", *paths], check=True)
PY
```

For shared-loop overhead changes, also run:

```bash
uv run pytest tests/benchmark/test_assistant_loop.py tests/benchmark/test_assistant_engine.py \
  tests/benchmark/test_governed_studio_performance.py
```

### Dedicated unpatched metadata/integration stack

Use a dedicated Compose project and preserve other checkouts' containers/volumes.
From `backend/`, export endpoints before Compose, seed, and pytest. Required
SQL/Redis/storage ports are `45930`, `45379`, and `45900`; auxiliary bindings also
avoid defaults.

The remediation acceptance setup provisions both stacks independently:

| Stack | FE / Redis / storage host ports | Engine |
| --- | --- | --- |
| Stock integration | `45930` / `45379` / `45900` | StarRocks `4.1.4`, test FE heap `2048` MiB |
| Governed acceptance | `47930` / `47379` / `47900` | Patched StarRocks `4.1.4`, including the task active-role patch |

Both use unique containers and volumes. Provisioning and patch verification do
not establish a passing runtime gate; record test results against the final
source/configuration after all implementation changes. Acceptance Compose files
and credentials remain untracked. The analytical sandbox has its separate
`BLOCKED_BY_INFRASTRUCTURE` status; these engine stacks do not supply isolation
for Python analysis.

```bash
export COMPOSE_PROJECT_NAME=nova-governed-studio-l3
export NOVA_TEST_FE_HEAP_MB=2048
export NOVA_TEST_FE_MYSQL_PORT=45930
export NOVA_TEST_FE_HTTP_PORT=45830
export NOVA_TEST_FE_ARROW_PORT=45940
export NOVA_TEST_REDIS_PORT=45379
export NOVA_TEST_MINIO_PORT=45900
export NOVA_TEST_MINIO_CONSOLE_PORT=45901
export STARROCKS_HOST=127.0.0.1
export STARROCKS_FE_MYSQL_PORT=45930
export STARROCKS_HTTP_PORT=45830
export STARROCKS_ROOT_USER=root STARROCKS_ROOT_PASSWORD=
export STARROCKS_PORT=45930 S3_PORT=45900
export REDIS_URL=redis://127.0.0.1:45379/0
export S3_ENDPOINT=http://127.0.0.1:45900
export RANGER_ENABLED=false
export SECRET_KEY="$(uv run python -c 'import secrets; print(secrets.token_hex(32))')"
export FERNET_KEY="$(uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
# Docker Desktop on macOS; Linux uses network=host and host=127.0.0.1.
export NOVA_PROXY_E2E_NETWORK=bridge
export NOVA_PROXY_E2E_HOST=host.docker.internal
docker pull mysql:8.0
docker compose -f docker-compose.test.yml config --quiet
docker compose -f docker-compose.test.yml up -d --wait
bash tests/integration/seed_engine.sh
uv run pytest tests/integration -m engine -v -rs \
  --log-cli-level=WARNING --junit-xml=integration.xml
```

Focused persistence/projection checks:

```bash
uv run pytest tests/integration/test_governed_studio_metadata.py \
  tests/integration/test_studio_business_metadata.py -m engine -v -rs \
  --junit-xml=governed-studio-metadata.xml
```

Inspect collection, failures, errors, and skips in each JUnit report. Zero
collected or all skipped is not a pass. The unpatched suite normally skips the
separately opted-in Ranger module, leaving that gate unmet. This stack proves
metadata/native-engine behavior only. MinIO moves both sides of its binding
because `FILES()` endpoint rewriting keeps the port.

The integration CI job and the commands above use a 2 GiB FE heap. The complete
metadata journey encountered sustained GC pressure with the default 1 GiB heap:
recorded pauses
occupied about 57 seconds of the minute in which a source-access check timed
out. `NOVA_TEST_FE_HEAP_MB` changes only the isolated test FE's heap; application
deadlines, auditing, and authorization checks stay unchanged. Other invocations
retain the 1 GiB default. Reserve sufficient host memory for the FE, BE, and the
test runner before increasing this value.

The pinned stock FE needs `enable_udf=true` in its test configuration to apply
the bootstrap's existing AI/ML function declarations. Configure and restart only
the isolated FE before checking the complete bootstrap. Inspect its SQL errors
directly; a successful seed exit does not establish successful function creation.
Set `NOVA_CUSTOM_SQL_LIVE=1` for the additional live custom-SQL acceptance checks.

Tear down only this project using the same exported environment:

```bash
docker compose -f docker-compose.test.yml down
```

### Patched-FE Ranger gate

Use a separate initialized, disposable patched-FE/Ranger installation with the
current patch/image pins, policy bridge/bootstrap, `NOVA_SYSTEM`, Redis, and
authorized task execution. The governed Compose file has fixed container names,
explicit volume names, and additional host ports; changing its project name
alone does not isolate a second stack. Use an isolated Docker context/VM or an
untracked full Compose configuration prepared from the governed file. Give every
container, named volume, and network a unique acceptance prefix, remap every
published host port, and preserve the relative bind/build paths through Compose's
`--project-directory`. Keep both the Ranger role-context patches and the task
active-role patch. The local remediation FE artifact is tagged
`task-role-20260926`; its engine/source pin remains `4.1.4` at the commit in the
[patch README](../patches/starrocks/README.md). Follow
[Ranger setup](29-ranger-access-control.md#operations) and
the [patch guide](../patches/starrocks/AGENTS.md). The stock L3 FE cannot satisfy
this gate.

Patch application check, from the repository root:

```bash
./patches/starrocks/verify.sh
```

For reproducible isolation on the same Docker host, set
`NOVA_GOVERNED_COMPOSE_FILE` to that untracked full configuration and
`NOVA_GOVERNED_ACCEPTANCE_ENV` to its untracked Compose environment. Use
`NOVA_GOVERNED_PROJECT` as the prefix of every explicit container/volume/network
name. Configure the host test environment to use FE `47930`, Redis `47379`, and
storage `47900`, with distinct auxiliary ports. From the repository root, check
isolation without printing the interpolated configuration or credentials:

```bash
: "${NOVA_GOVERNED_COMPOSE_FILE:?set the isolated full Compose configuration}"
: "${NOVA_GOVERNED_ACCEPTANCE_ENV:?set the isolated Compose environment}"
export NOVA_GOVERNED_PROJECT="${NOVA_GOVERNED_PROJECT:-nova-studio-ranger-acceptance}"
uv run --directory backend python - <<'PY'
import json
import os
import subprocess
from pathlib import Path

prefix = os.environ["NOVA_GOVERNED_PROJECT"]
command = [
    "docker", "compose", "--project-directory", str(Path.cwd().parent / "docker"),
    "--project-name", prefix, "--env-file", os.environ["NOVA_GOVERNED_ACCEPTANCE_ENV"],
    "-f", os.environ["NOVA_GOVERNED_COMPOSE_FILE"], "--profile", "app", "config",
]
result = subprocess.run(command + ["--format", "json"], check=True, capture_output=True)
config = json.loads(result.stdout)
for service in config["services"].values():
    assert service["container_name"].startswith((prefix + "-", prefix + "_")), "shared container name"
    for port in service.get("ports", []):
        assert int(port["published"]) not in {
            8000, 4406, 6080, 8983, 9000, 9001, 6379, 8030, 9020, 9408, 8040, 9050
        }, "shared host port"
for section in ("volumes", "networks"):
    for value in config.get(section, {}).values():
        assert not value.get("external"), "external acceptance resource"
        assert value["name"].startswith((prefix + "-", prefix + "_")), "shared resource name"
print("Isolated governed configuration checked; runtime acceptance remains required.")
PY
docker compose --project-directory "$PWD/docker" \
  --project-name "$NOVA_GOVERNED_PROJECT" \
  --env-file "$NOVA_GOVERNED_ACCEPTANCE_ENV" \
  -f "$NOVA_GOVERNED_COMPOSE_FILE" --profile app config --quiet
docker compose --project-directory "$PWD/docker" \
  --project-name "$NOVA_GOVERNED_PROJECT" \
  --env-file "$NOVA_GOVERNED_ACCEPTANCE_ENV" \
  -f "$NOVA_GOVERNED_COMPOSE_FILE" --profile app up -d --wait
```

With that isolated app profile running, the existing role/proxy/filter/mask/root
guard check uses the same configuration:

```bash
docker compose --project-directory "$PWD/docker" \
  --project-name "$NOVA_GOVERNED_PROJECT" \
  --env-file "$NOVA_GOVERNED_ACCEPTANCE_ENV" \
  -f "$NOVA_GOVERNED_COMPOSE_FILE" exec nova-backend \
  python scripts/verify_ranger_e2e.py
```

In a fresh host shell, load an untracked shell-compatible environment for that
isolated installation. It must supply FE host/port/operator credentials,
`REDIS_URL`, storage/configuration settings, stable `SECRET_KEY`/`FERNET_KEY`, and
Ranger Admin URL/service/credentials/TLS settings. Do not reuse the stock L3
environment. Then, from `backend/`:

```bash
: "${NOVA_GOVERNED_ACCEPTANCE_ENV:?set the path to the isolated acceptance environment}"
set -a
source "$NOVA_GOVERNED_ACCEPTANCE_ENV"
set +a
export RANGER_ENABLED=true
export RANGER_STRICT_SINGLE_ACTIVE_ROLE=true
export NOVA_GOVERNED_STUDIO_RANGER_ACCEPTANCE=1
uv run pytest tests/integration/test_governed_studio_ranger.py -m engine -v -rs \
  --log-cli-level=WARNING --junit-xml=governed-studio-ranger.xml
```

The opted-in fixture enables workflow/actions/quality locally and creates live
roles, users, policies, data, schedules, and business policy state. It is not a
production probe. It exercises release pins with live role/filter/mask/revocation
behavior, scoped Missions/attachments, production scoring with execution binding,
and supervised Action consent/readback/compensation. Inspect collected assertions
and the report; an unset opt-in variable skips the module. Mocks, policy writes,
and patch application alone cannot satisfy this gate.

For a bounded journey timing run, reuse the same isolated environment and
unchanged acceptance contract:

```bash
uv run python -m tests.benchmark.governed_studio_journey --samples 3 \
  --report /tmp/nova-governed-studio-journey.json
```

The report measures the pytest call phase through live SQL/Ranger and in-process
HTTP APIs. Setup and cleanup are excluded; semantic planning and observations
are scripted. Three samples provide a smoke measurement, with p95 equal to the
largest sample. Failures or skips invalidate the report. Complete journey counts
remain unavailable; the separate native-loop fixture measurements cannot supply
them. Do not present these timings as a production latency guarantee.

### Frontend and controlled workflow

From `frontend/`, run sequentially with CI's browser setup:

```bash
pnpm install --frozen-lockfile
pnpm exec playwright install --with-deps chromium
pnpm lint
pnpm build
pnpm test:coverage
```

Record revenue decline through Mission, semantic evidence, hypotheses, scenarios,
Decision approval, monitor Action, verification, Outcome, and persisted
quality/context references. Include denial, cancellation, reconnect, and selective
nested attachment delegation. Inspect empty, loading, partial, error,
permission-disabled, populated, keyboard/focus, 320px/desktop, and light/dark
states. Scripted backend checks are separate from browser evidence.

Measure provider/tool calls, participants, context size, metadata reads, scoring
overhead, and controlled p50/p95 where applicable. Record workload/sampling
conditions; microbenchmarks do not establish production latency or causal business
gains. Exclude arguments, raw attachments, secrets, and private reasoning from
telemetry/reports.

## Analytical executor prerequisite

`GET /api/v1/agents/studio/analysis-workspace/capability` reports unavailable for
the shipped executor. `POST .../executions` returns an unavailable result with
`503`; toggling the flag alone cannot provide isolation.

A future adapter must enforce CPU/memory/wall/output limits outside the request
process, deny network access, isolate filesystem/package access, terminate and
clean up on cancellation, accept only granted credential-free inputs, and return
validated artifacts through authorized `@stage` references. Exercise escape,
resource-limit, cancellation, and cleanup failures before enabling it.
Fake-executor contract tests and the thread-based ML runtime do not establish
production sandbox isolation.
