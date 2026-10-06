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
checkout's volumes to make tests pass; use the isolated-stack guidance in the
run guide. Documentation-only edits need documentation checks, not a deployment.
