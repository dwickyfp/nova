# Superset feature and dependency inventory

Audited 2026-11-08 on the governed development stack (compose project `docker`,
Superset 6.1.0, image `nova-superset:local` id `sha256:81a69921…`) before any
lean change (OPS-001, OPS-005). Every classification below names its evidence;
nothing was removed on a code search alone. Usage counts are from the
Superset metadata database `logs` table (last 30 days of this development
instance) and its object tables; they show that a path is exercised, not
production volume.

Classification: **REQUIRED** (an agreed feature breaks without it),
**CONDITIONAL** (needed only while a named feature stays enabled),
**REMOVABLE** (no agreed feature uses it).

## Services and packages

| Component | Class | Evidence | Lean decision |
| --- | --- | --- | --- |
| Superset web (gunicorn, 1 worker × 20 gthreads) | REQUIRED | Full app in Dataviz: dashboards, Explore, SQL Lab, datasets, REST API used by Nova's publisher and context service | Kept; image without Chromium |
| Superset metadata PostgreSQL 16 | REQUIRED | Holds every chart, dataset and dashboard (D1 of arch-17). Idle docker memory ≈ 9 MiB | Kept unchanged |
| `superset-init` (one-shot) | REQUIRED at deploy/upgrade | Runs `superset db upgrade`, roles and the "Nova" database URI; exits 0 in 33 s | Kept; not resident. Uses the web image |
| `superset-worker` (Celery) | REQUIRED | **Async SQL Lab**: database "Nova" has `allow_run_async = true`; `SqlLabRestApi.get_results` 31×, `execute_sql` 108×. **Thumbnails**: `DashboardRestApi.thumbnail` 66×, `ChartRestApi.thumbnail` 15×, `screenshot.compute.*` 7×; Studio's artifact cards show them (`frontend/src/features/studio/artifact-explorer.tsx`). **Alerts/reports**: feature ships (arch-17); 2 schedules created and deleted by acceptance | Kept, right-sized: `--autoscale=2,1`, `--max-memory-per-child`, broker-only health check |
| `superset-beat` | CONDITIONAL on alerts/reports | `reports.scheduler` every minute; `reports.prune_log` daily. 0 report schedules exist now, but the feature is agreed and users can create them in the editor | Kept while alerts/reports ship; ticks now expire (see finding F1) |
| Playwright + Chromium | REQUIRED in the worker only | Thumbnails and report screenshots run in Celery tasks. The web app, beat and init never start a browser | Moved out of the web image (`web` target); kept in the worker's `jobs` target |
| Redis (Nova's shared instance) | REQUIRED | Nova sessions/locks (db 0), Superset metadata and filter-state cache (db 3), data and thumbnail cache (db 4), SQL Lab results (db 5), Celery broker/results (db 6) | Kept; see finding F2 for eviction risk |
| StatsD metrics (`nova_superset/metrics.py`) | CONDITIONAL on the observability stack | Packets dropped when `statsd-exporter` is absent; no resident service added | Unchanged |
| psycopg2, PyMySQL, starrocks drivers | REQUIRED | Metadata DB and the "Nova" connection through Nova's MySQL proxy | Unchanged |

## Superset features

| Feature | Flag / config | Callers | Worker | Beat | Browser | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Dashboard viewer and editor | default | Dataviz tab, `/analytics`, `/bi` | – | – | – | Kept |
| Native filters, filter scope, cross-filters, drill | default | Dataviz; filter state read by Nova's context service via `/api/v1/dashboard/<id>/filter_state/<key>` | – | – | – | Kept |
| Explore / chart editing | default | Dataviz, artifact edit by prompt (`/explore/form_data`) | – | – | – | Kept |
| Chart data API (sync) | `GLOBAL_ASYNC_QUERIES=False` | Every chart load; Nova publisher validation | – | – | – | Kept sync |
| SQL Lab sync | default | Users | – | – | – | Kept |
| SQL Lab async | database `allow_run_async` | Users (`get_results` observed) | **yes** | – | – | Kept |
| Dashboard/chart thumbnails | `THUMBNAILS=True`, `PLAYWRIGHT_REPORTS_AND_THUMBNAILS=True` | Studio artifact cards, Superset list views | **yes** | – | **yes** | Kept |
| Alerts and reports | `ALERT_REPORTS=True` | Superset editor (report modal checked on every dashboard load) | **yes** | **yes** | **yes** | Kept |
| Cache warmup | not scheduled (S9) | – | – | – | – | Off (unchanged) |
| CSV/XLSX export | default | Users (`ChartRestApi.export` 3×) | – | – | – | Kept |
| Embedded SDK / guest tokens | `EMBEDDED_SUPERSET=False` | Not used (S14) | – | – | – | Off (unchanged) |
| Semantic View datasets | `nova_superset/semantic_guard.py` | Nova semantic sync | – | – | – | Kept |
| AI publication (Nova) | Nova backend | Add to Dataviz, assistant tools | – | – | – | New, uses web REST only |

No agreed feature was retired. Nothing in Nova was given an on/off flag.

## Findings

**F1. Beat filled Redis while the worker was down (defect, fixed).** On
2026-11-08 the worker had been stopped for 22 hours (warm shutdown, exit 137)
while beat kept running: `LLEN celery` in db 6 was **1 323**, all
`reports.scheduler` ticks with no `expires`. A restarted worker would have
replayed every one. Fix: each beat entry now expires before the next tick
(`superset_config.py`, `expires: 55` / `3600`). The stale ticks were deleted
before measuring; they carried no user data (0 report schedules).

**F2. Shared Redis eviction (risk, documented).** Redis runs
`maxmemory 256mb`, `allkeys-lru`; measured use is 3.3 MiB with 0 evictions.
Superset caches and the broker share the budget with Nova sessions, so a cache
flood could evict a session. No change made: the measured margin is ~77×, and
changing the policy affects Nova's own keys. Recommended next step: a separate
`maxmemory` for BI caches if production data volumes approach the limit.

**F3. The worker health check was the largest idle cost (fixed).**
`celery --app=superset… inspect ping` imports the whole Superset application
in a new process: measured **13.9 CPU-seconds and 258 MB peak RSS per check,
every 30 s** (≈ 46 % of a core). A broker-only ping proves the same thing (the
worker answers on its broker) in **1.6 CPU-seconds**; the interval is now 60 s.

**F4. Every chart query costs ≈ 1 s even from cache (measured, not changed).**
A cached chart-data request takes 983–1 091 ms while metadata requests take
19–53 ms. Each request opens a new SQLAlchemy engine through Nova's proxy and
SQLAlchemy's MySQL dialect initialisation runs about eight probe statements
(`SELECT CURRENT_VERSION()`, `SHOW VARIABLES LIKE …`, `ROLLBACK`, …), each one
through QueryService and an `AUDIT_LOG` write. This dominates dashboard load
time under concurrency (see the benchmark). It sits in the governed proxy and
audit path, so it is not changed here; it is the first performance milestone
(serve dialect probes per connection without weakening audit, or reuse engines
per data token).

**F5. Host development could not reach BI (fixed).** With the backend on the
host (`dev.sh`), Superset still called `http://nova-backend:8000` and the
backend called `http://superset:8088`; every BI request answered 503.
`docker-compose.dev.yml` now points Superset at the host and `dev.sh` gives
the backend the loopback URL and the service token from the running container.
