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
- Use typed Python interfaces, Pydantic API schemas, and established dataclasses
  where appropriate. Follow existing async `asyncmy` connection/cursor lifetimes
  through `backend/app/core/database.py`; do not introduce a synchronous driver
  or a second connection factory.
- System connections may serve control-plane/bootstrap/health operations.
  Caller-visible data uses the existing delegated execution path and security
  context. Preserve cancellation, sanitized errors, and redacted audit logging.
- Before changing durable metadata, inspect `docker/init-nova.sql`,
  `backend/app/common/nova_system.py`, and the owner's schema initialization.
  CRUD configuration uses existing primary-key conventions. Audit/analytics
  table models must follow their actual schema, not a generic table template.
- Configuration comes through `backend/app/core/config.py`; reuse existing
  encryption and `backend/app/storage/secrets.py` resolution. Never persist
  plaintext credentials as a shortcut when encryption or resolution fails.

## Cross-scope routing

Read these guides even when the edited file is outside their directory:

| Change | Additional guide |
| --- | --- |
| Query service, proxy, ML SQL preparation, grammar, dialect helpers, SQL tests | [SQL frontend](app/sql_frontend/AGENTS.md) |
| Auth, role gates, Ranger integration, stage authorization, user-data adapters | [Access control](app/modules/access_control/AGENTS.md) |
| Assistant loop, providers, tool registry, consent, evals | [Assistant](app/modules/assistant/AGENTS.md) |
| Studio orchestration, Smart workers, resources, semantic agent tools | [Agents](app/modules/agents/AGENTS.md) |
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
`uv run ruff check .` and `uv run mypy app --no-error-summary` expose report-only
baselines; do not mistake them for clean blocking gates or hide new failures.

For DB access, SQL execution, auth, storage, or proxy changes, run the real-engine
suite with CI's services, seed data, and environment. Start an isolated test
stack, not production services. From `backend/`:

```bash
docker pull mysql:8.0
docker compose -f docker-compose.test.yml up -d --wait
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
