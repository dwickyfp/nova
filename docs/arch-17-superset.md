# Architecture 17: Superset (removed)

> Status: **historical. Apache Superset was decommissioned on 2026-11-16.**
> Native Dataviz is the only chart and dashboard engine. This page remains so
> older links, reports and commits that cite it still resolve.

## What it was

From 2026-10-29 to 2026-11-16 Nova shipped Apache Superset as an embedded BI
surface at `/bi` and in Studio's Dataviz tab. Superset ran as separate
containers (web, Celery worker, Celery beat, a one-shot bootstrap and its own
PostgreSQL metadata database), reached Nova through the MySQL proxy under the
signed-in user's identity, and exported statsd metrics to a Grafana dashboard.
Its image, configuration and `nova_superset` package lived in the top-level
`superset/` directory.

## What replaced it

- Saved charts, dashboards and governed datasets are native Nova documents
  owned by `backend/app/modules/viz/` and rendered by
  `frontend/src/features/native-dataviz/`. See the
  [native visualization guide](../backend/app/modules/viz/AGENTS.md) and the
  [implementation boundary](architecture/native-dataviz/IMPLEMENTATION.md).
- Studio's Dataviz surface is described in
  [Architecture 19](arch-19-studio-dataviz.md); dashboards follow the active
  role as before.
- Chart and dashboard queries run through Nova's SQL path as the caller, so
  Ranger filters, masks and audit apply without a second BI process.

## Removed with it

- Compose services `superset-db`, `superset-init`, `superset`,
  `superset-worker`, `superset-beat`, the `superset_db_data` volume, the
  statsd exporter and the `/bi/` gateway route.
- The Superset settings in `docker/.env.example`, the PostgreSQL client in the
  backend image that dumped Superset's metadata, the Superset CI job, the
  Grafana **Nova | BI (Superset)** dashboard and the Superset operations and
  benchmark documents.

Existing deployments that still hold a `docker_superset_db_data` volume can
keep it as an archive or remove it once nothing needs its charts. Nova no
longer reads it.
