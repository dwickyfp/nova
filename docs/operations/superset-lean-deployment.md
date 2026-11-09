# Superset lean deployment

The BI services Nova runs, why each one is there, and how to start, upgrade,
back up and roll them back. The inventory and its evidence are in
[superset-feature-inventory.md](superset-feature-inventory.md); measured
before/after numbers are in
[benchmarks/superset-lean/](../benchmarks/superset-lean/comparison.md).

## Topology

| Service | Image | Resident | Purpose |
| --- | --- | --- | --- |
| `superset-db` | `postgres:16.10-alpine` | yes | Superset metadata (charts, datasets, dashboards) |
| `superset-init` | `nova-superset:local` (`web` target) | no, exits 0 | Migrations, roles, the "Nova" database |
| `superset` | `nova-superset:local` (`web` target) | yes | The web app and REST API under `/bi` |
| `superset-worker` | `nova-superset-jobs:local` (`jobs` target) | yes | Async SQL Lab, thumbnails, alerts and reports |
| `superset-beat` | `nova-superset:local` (`web` target) | yes | Schedules alerts and reports; every tick expires |
| `redis` | `redis:7-alpine` | yes (shared with Nova) | Caches, SQL Lab results, Celery broker |

What changed from the previous deployment, and what did not:

- The web image no longer carries Playwright or Chromium; only the worker's
  `jobs` image does. Nothing that starts a browser runs in `superset`,
  `superset-init` or `superset-beat`.
- The worker health check pings the worker over its broker instead of loading
  the Superset application in a new process every 30 s.
- The worker runs one process when idle and a second under load
  (`--autoscale=2,1`) and recycles a process above 700 MiB
  (`--max-memory-per-child=716800`).
- Beat ticks expire before the next one, so a stopped worker no longer fills
  Redis with stale scheduler ticks.
- No feature, flag, role or security hook was removed. Alerts, reports,
  thumbnails, async SQL Lab, native filters, the editor and SSO all remain.

## Start

From the repository root, with `docker/.env` holding the Superset secrets
(`SUPERSET_SECRET_KEY`, `SUPERSET_DB_PASSWORD`, `NOVA_SUPERSET_SERVICE_TOKEN`):

```bash
docker compose -f docker/docker-compose-engine.yml --profile app build superset-init superset-worker
docker compose -f docker/docker-compose-engine.yml --profile app up -d superset-db superset-init superset superset-worker superset-beat
```

For host development (`./dev.sh`), add the development override so Superset
reaches the backend on the host, and use the running project's name:

```bash
docker compose -p docker -f docker/docker-compose-engine.yml -f docker/docker-compose.dev.yml --profile app up -d --build --no-deps superset-init superset superset-worker superset-beat
```

`superset-init` must exit 0 before `superset` and the worker start (compose
waits on `service_completed_successfully`). Re-running it is safe: migrations,
roles and the "Nova" database are idempotent. It rewrites the "Nova" database
URI from `NOVA_PROXY_HOST`, so run it again whenever the proxy host changes
(for example when switching between host development and the `app` profile).

Health:

```bash
curl -fsS http://127.0.0.1:8088/bi/health
docker compose -p docker -f docker/docker-compose-engine.yml ps superset superset-worker superset-beat
```

The automated smoke check is `superset/bench/lean_smoke.py` (structured JSON).

## Upgrade

1. Bump `FROM apache/superset:<version>` in `superset/Dockerfile` and
   `SUPERSET_VERSION` in `backend/app/modules/superset/charts.py`; re-check
   every viz type in `charts._VIZ_TYPES` against the new frontend bundle and
   the hooks named in `docs/arch-17-superset.md`.
2. Back up (below), build both targets, run `superset-init`, then recreate
   `superset`, `superset-worker` and `superset-beat`.
3. Run the smoke check and the governed acceptance
   (`backend/tests/integration/test_superset_governed.py`).

## Backup and restore

Superset metadata travels with `NOVA_SYSTEM` snapshots
(`backend/app/modules/backup/bi_metadata.py`, a `pg_dump` stored in managed
object storage). A manual copy before any change:

```bash
docker exec nova-superset-db pg_dump -U superset -d superset -Fc > superset-metadata-$(date +%Y%m%d%H%M).dump
```

Restore into the stopped web and worker, then start them again:

```bash
docker compose -p docker -f docker/docker-compose-engine.yml stop superset superset-worker superset-beat
docker exec -i nova-superset-db pg_restore -U superset -d superset --clean --if-exists < superset-metadata-YYYYMMDDHHMM.dump
docker compose -p docker -f docker/docker-compose-engine.yml start superset superset-worker superset-beat
```

Nova's own publication journal (`NOVA_SYSTEM.CONFIG_DATAVIZ_PUBLICATIONS`) and
artifact links (`CONFIG_STUDIO_ARTIFACTS`) are part of the `NOVA_SYSTEM`
snapshot; restore both together so chart ids keep matching.

## Rollback

The previous image is kept as `nova-superset:baseline-6.1.0`. No metadata
schema changed, so rolling back is a configuration change only:

```bash
git checkout <previous-commit> -- superset/Dockerfile superset/superset_config.py docker/docker-compose-engine.yml
docker tag nova-superset:baseline-6.1.0 nova-superset:local
docker compose -p docker -f docker/docker-compose-engine.yml -f docker/docker-compose.dev.yml --profile app up -d --no-deps superset superset-worker superset-beat
```

Every chart, dashboard and Nova link stays valid: the services read the same
metadata database and Redis.

## Capacity guards

- The worker recycles a process above 700 MiB and runs at most two.
- Superset web runs gunicorn with one worker and 20 threads (the upstream
  default). The load benchmark shows chart queries, not web threads, as the
  bottleneck (finding F4 in the inventory), so the thread count is unchanged.
- Redis keeps `maxmemory 256mb` with `allkeys-lru`; measured use is 3.3 MiB.
  See finding F2 before raising BI cache volumes.
