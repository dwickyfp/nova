# Infrastructure Agent Guide

Inherit the [root contracts](../AGENTS.md). Paths below are repository-relative;
commands run from the repository root unless stated otherwise. Read
[HOW_TO_RUN.md](../HOW_TO_RUN.md) for local setup and operational prerequisites.

## Topology and state

- `docker/docker-compose-engine.yml` owns the governed local stack: patched
  StarRocks FE, pinned BE, Ranger services/policy bridge/bootstrap, Redis, and
  object storage. Its `app` profile adds the backend and task/migration worker.
  Do not assume it also starts every scheduler or Studio worker; inspect
  `dev.sh` and the run guide for separate process ownership.
- `docker/docker-compose.split.yml` is an opt-in override that replaces the
  single backend with scalable web, query and MySQL proxy tiers behind the
  `docker/gateway/` nginx gateway. `NOVA_PROCESS_ROLE` decides which startup
  duties a process runs; only the web role bootstraps schemas, under a Redis
  lock. Assistant and agent routes are pinned to one query replica per session
  because their consent and cancel state is process-local; do not replace that
  rule with round robin or with `hash ... consistent`, which does not pin
  replicas that share one DNS name. `/api/v1/internal/` is never routed publicly.
  `gateway/tls/` holds deployment-local certificates and is git-ignored.
- Local frontend development uses Vite 5173 and backend 8000; the MySQL proxy
  defaults to 4406. Compose/environment files own deployment port bindings.
  The engine's native MySQL port stays internal in governed deployment;
  `docker/docker-compose.dev.yml` is an explicit development override.
- Use the current image/build pins and
  [StarRocks patch guide](../patches/starrocks/AGENTS.md). Replacing the patched
  FE with a stock image can remove role/filter/mask enforcement even when it
  starts successfully. Keep FE/BE/grammar capability assumptions aligned.
- Ranger owns its own policy database and services. That database is not a Nova
  relational metadata store. Redis coordinates runtime/session work; object
  storage carries payloads; durable relational Nova metadata uses `NOVA_SYSTEM`.
- `docker/init-nova.sql` and `backend/app/common/nova_system.py` own bootstrap
  and schema initialization. Preserve flat table naming, additive compatible
  changes, role guards/defaults, and bootstrap order. Do not copy old schema
  diagrams or invent SQL unsupported by the pinned engine.
- Tracked configuration contains metadata, placeholders, and secret references.
  Deployment keys belong in untracked environment/supported secret stores.
  Share stable signing/encryption configuration across applicable processes;
  do not generate replacement keys silently at process startup.
- Keep the authenticated Ranger policy bridge internal and allowlisted. Do not
  expose root, raw FE access, or operator credentials through public Nova paths.
  Preserve health/readiness checks and existing safe development overrides.

## Reuse running stacks

Local tests and checks run on the containers already up on the machine. Do not
start another stack next to them.

- Find them first. `docker compose ls` lists each project with its compose
  files; `docker ps` shows host ports. Use the project that runs the file you
  need, whatever its name, and pass its `-p <project>` and the same `-f` files
  on every compose command so the command acts on those containers.
- Inspect selected test fixtures and scripts before running them: automatic
  Compose/Testcontainers provisioning also counts as creating a stack. Set
  their endpoints to existing compatible services and verify they will reuse
  them without teardown. Unreachable default ports do not prove no stack exists.
- Match the stack to the work. The `docker/docker-compose-engine.yml` project is
  the governed development stack and holds development data: use it for live
  Studio/Smart, Ranger, and UI checks, never for suites that drop or reseed
  data. The `backend/docker-compose.test.yml` project is the unpatched test
  stack: reuse it when already running, with its host ports set through the
  `NOVA_TEST_*` and endpoint variables in [HOW_TO_RUN.md](../HOW_TO_RUN.md).
  If only the governed stack is running, use it for compatible, non-destructive
  checks. Report incompatible suite requirements and ask before creating a
  separate test stack; a different compose file does not authorize duplication.
- Apply new work to the running stack:
  - Backend, worker, and frontend run as host processes in local development
    (`dev.sh` or a local launch configuration). Restart them to load new
    code. Backend startup runs the `NOVA_SYSTEM` bootstrap, so a restart also
    applies additive schema changes to the running engine.
  - After seed or test-schema changes, rerun
    `backend/tests/integration/seed_engine.sh` with `COMPOSE_PROJECT_NAME` and
    `STARROCKS_PORT` set to the running test project.
  - For image, Dockerfile, patch, or service configuration changes, rebuild and
    recreate only the changed service inside the same project:
    `docker compose -p <project> -f <files> up -d --build --no-deps <service>`.
    Its volumes and the other containers stay as they are. A profile service the
    project lacks, such as the `app` profile, is added to that same project.
  - If a compose file that `docker compose ls` lists for the project no longer
    exists, ask before recreating any of its services, because recreating
    without that override changes the service configuration.
- A port conflict, leftover data, or another checkout is not a reason to start
  a new project. Reuse compatible services at their actual ports; reseed only
  an existing disposable test stack. Start a local stack automatically only
  when no Nova stack is running and initialization is safe; use its default
  project name and leave it running for the next check. When a check needs
  fresh initialization, a data reset on a shared stack, or a separate acceptance
  stack, ask the user first. CI startup/teardown applies to its ephemeral runner,
  not the developer's running stack.
  The dedicated project names and shifted ports in
  [HOW_TO_RUN.md](../HOW_TO_RUN.md) and
  [governed Studio operations](../docs/governed-studio-operations.md) describe
  how to start a stack when none runs, not a second stack beside a running one.
- Helper containers that a test removes itself (`--rm`), such as the MySQL
  client in proxy tests, are fine. Remove any helper container you start
  yourself once the check is done.

## Validation and operational boundaries

Read [CI](../.github/workflows/ci.yml) and the
[backend guide](../backend/AGENTS.md) when changing test infrastructure.
`backend/docker-compose.test.yml` is a separate unpatched integration stack;
use its endpoint overrides, seed script, MySQL client image, and test-report
guards. It does not prove governed Ranger behavior.

For Compose changes, validate interpolation without printing secret-bearing
configuration, using the required local environment:

```bash
docker compose -f docker/docker-compose-engine.yml config --quiet
docker compose -f backend/docker-compose.test.yml config --quiet
```

Then run the affected container startup/readiness and integration checks.
Governed behavior additionally follows the
[access-control guide](../backend/app/modules/access_control/AGENTS.md) and
patched-FE acceptance setup. Preserve the distinction between successful policy
writes and observed FE enforcement; Nova audit remains separate from FE Ranger
audit destination support.

Schema/seed changes require real-engine checks for fresh initialization and
existing-state compatibility as applicable. Patch/image changes require patch
verification and governed acceptance. Never reset shared data or remove another
checkout's volumes to make tests pass; reuse the running stacks as described
above. Documentation-only edits need documentation checks, not a deployment.
