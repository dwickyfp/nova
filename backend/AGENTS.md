# Backend Agent Guide

Inherit the [root contracts](../AGENTS.md). Paths below are repository-relative;
commands run from `backend/` unless marked otherwise.

## Ownership and implementation

- Python/FastAPI application code belongs in the existing feature domains under
  `backend/app/modules/`. Routers validate HTTP I/O, services own domain behavior,
  and existing repositories/adapters own persistence and external I/O. Follow
  the owning module rather than creating a parallel abstraction.
- Runtime/dependency truth is `backend/.python-version`,
  `backend/pyproject.toml`, and `backend/uv.lock`. The local Python pin is 3.11.
  Use `uv sync --locked`; do not copy dependency lists into instructions.
- Use typed Python interfaces, Pydantic API schemas, and established dataclasses.
  StarRocks access retains async `asyncmy` lifetimes through
  `backend/app/core/database.py`. Approved PG metadata access uses async psycopg3
  through the sole `backend/app/core/metadata_db.py` owner. Domain repositories
  use explicit actor-bound transactions; do not add synchronous metadata I/O,
  another pool owner, or a SQL dialect-rewrite proxy.
- System connections may serve control-plane/bootstrap/health operations.
  Caller-visible data uses the existing delegated execution path and security
  context. Preserve cancellation, sanitized errors, and redacted audit logging.
- Before changing durable metadata, inspect `docker/init-nova.sql`,
  `backend/app/common/nova_system.py`, and the owner's schema initialization.
  CRUD configuration uses existing primary-key conventions. Audit/analytics
  table models must follow their actual schema, not a generic table template.
- Follow the [metadata guide](metadata_migrations/AGENTS.md) for PG transactions,
  grants/RLS, imports and cutover. Prepared schemas, repositories and services
  remain distinct from active runtime callers. Preserve the current authority
  until verified cutover, without dual-write or uncertain-effect retries.
- Configuration comes through `backend/app/core/config.py`; reuse existing
  encryption and `backend/app/storage/secrets.py` resolution. Never persist
  plaintext credentials as a shortcut when encryption or resolution fails.

## Adding an app or domain

- Start from the root workflow and existing module ownership. A new app surface
  calls shared services; it does not acquire a second user store, policy engine,
  SQL path, object-store client framework, scheduler or inference loop.
- Keep request validation, domain commands, persistence and external adapters
  explicit. The service owns the transaction and effect lifecycle. Routers and
  tools call that service; they do not bypass its authorization or write the
  domain's tables directly. Introduce a shared abstraction only for a concrete
  shared responsibility or an approved replacement.
- Define actor/scope, object identity, compatibility, revision/idempotency and
  error contracts before wiring consumers. For durable commands, define the
  state transition, job/definition, outbox, receipt and recovery rules required
  by the effect. Reuse the existing durable-job infrastructure and domain owner.
- Assign long-running work to its owning runtime role. HTTP handlers submit
  intent and expose status/cancellation/recovery through established contracts.
  Configure bounded admission and dependency pools; do not use fire-and-forget
  tasks as durable jobs or run untrusted ML/Python inside the API.
- Trace every entrypoint that can reach the capability: UI/API, SQL/proxy,
  Notebook, assistant tools, schedules and maintenance where applicable. They
  must preserve the same identity and authorization, without duplicated logic.
  Register normal startup, routes/tool catalogs and docs together.
- Add domain-specific instructions only when distinct invariants or validation
  justify them. Link to root and cross-scope owners and update the task router;
  keep migration progress and test counts in evidence records, not `AGENTS.md`.

## Cross-scope routing

Read these guides even when the edited file is outside their directory:

| Change | Additional guide |
| --- | --- |
| PG schema, metadata transactions, grants/RLS, import, epoch/cutover | [Metadata](metadata_migrations/AGENTS.md) |
| Process lifecycle, HTTP/worker split, scheduler/proxy startup, scaling | [Runtime](app/worker/AGENTS.md), [Docker](../docker/AGENTS.md) |
| Query service, proxy, ML SQL preparation, grammar, dialect helpers, SQL tests | [SQL frontend](app/sql_frontend/AGENTS.md) |
| Auth, role gates, Ranger integration, stage authorization, user-data adapters | [Access control](app/modules/access_control/AGENTS.md) |
| Assistant loop, providers, tool registry, consent, evals | [Assistant](app/modules/assistant/AGENTS.md) |
| Studio agents, Missions, automations, resources, semantic agent tools | [Agents](app/modules/agents/AGENTS.md) |
| Workspace files, versions, shares, object publication | [Workspaces](app/modules/workspaces/AGENTS.md) |
| Notebook documents, packages, runtimes, data tokens | [Notebooks](app/modules/notebooks/AGENTS.md) |
| Autopilot recommendation, experiment and apply lifecycle | [Autopilot](app/modules/query_autopilot/AGENTS.md) |
| Test Compose, seed scripts, engine fixtures | [Docker](../docker/AGENTS.md) |

Current domain docs include [Intelligence](../docs/28-intelligence-foundation.md),
[native ML](../docs/28-native-ml-runtime.md), and
[task orchestration](../docs/08-task-manager.md). Historical backend architecture
examples do not determine current module layout or database APIs.

## Validation

Read [CI](../.github/workflows/ci.yml) for setup and gate status. Backend code
changes require the full unit/coverage run and eval scorecard after final edits:

```bash
uv sync --locked
uv run pytest tests/unit --cov=app --cov-branch --cov-report=term-missing --cov-report=xml:coverage.xml
uv run python -m tests.eval.report
```

Run `uv run ruff check --force-exclude` with the actual changed Python paths,
relative to `backend/`, as additional arguments. This is blocking. Full-tree
`uv run ruff check .` exposes a report-only baseline. Full-application
`uv run mypy app --no-error-summary` is blocking; do not hide failures.

PG storage, migration and caller-port changes also require the real PostgreSQL
suite selected by CI, including affected concurrency and grant/RLS cases. Read
the metadata guide and inspect fixtures before running them. A passing prepared
store test does not establish the owning API, object-store lifecycle or cutover.

For DB access, SQL execution, auth, storage, or proxy changes, run the real-engine
suite with CI's services, seed data, and environment. First inspect
`docker compose ls`, `docker ps`, and the selected fixtures' setup/teardown.
Reuse the existing compatible stack and map its actual ports to the test
endpoints. Destructive seed/reset suites require an existing disposable test
stack, never development or production data. If only the development stack is
running and a suite cannot safely use it, report the incompatibility and ask
before provisioning another stack ([Docker guide](../docker/AGENTS.md)).

Do not copy CI's unconditional stack startup into local test runs. Fixtures
such as `docker_services` in `tests/conftest.py` can start Compose when their
configured endpoints are unreachable; verify their reuse path before invoking
them. Do not select free ports merely to make automatic provisioning succeed.
After verifying the target is the existing disposable test stack, from `backend/`:

```bash
docker pull mysql:8.0
bash tests/integration/seed_engine.sh
uv run pytest tests/integration -m engine -v -rs --log-cli-level=WARNING --junit-xml=integration.xml
```

Set the test endpoints and real throwaway signing/encryption keys first, as
specified in CI and [HOW_TO_RUN.md](../HOW_TO_RUN.md). Match proxy client network
settings to the host. Inspect collection/skips and the report: an entirely
skipped suite is not a pass. Do not remove someone else's containers or volumes.
The ordinary test stack is unpatched and does not prove Ranger policy behavior;
use the access-control and patch guides for governed acceptance.

For user-data adapter changes also run
`uv run python scripts/check_user_data_system_access.py`. This is an additional
repository boundary check, not currently a CI job. Documentation-only edits
follow the root documentation-validation route.
