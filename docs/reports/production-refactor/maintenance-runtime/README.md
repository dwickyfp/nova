# Maintenance outside the HTTP API

Source parent `3f6b3789`; the tested application digest and command results are
in [commands.json](commands.json). PG runtime authority has not changed.

The default launcher and development Compose now include a seventh role,
maintenance. Search indexing, Catalog and News refresh, and expired ML artifact
cleanup run there. HTTP Search create/rebuild/retry persists pending work without
starting a local build. The maintenance poller discovers that durable legacy
state. Existing authorization checks remain before queue writes. A failed loop
fails its maintenance process and cancels its siblings; Search cleanup drains
build tasks even when the poller itself failed.

Three pre-change HTTP-lifecycle regressions failed as expected. The final change
passes 10,909 unit/coverage tests with zero skips, 358 agent evals, the 54/189
scorecard, 84 focused tests, mypy, Ruff on 129 changed Python paths, 12 monitoring
checks, both affected Compose configuration checks and the user-data boundary
check. All 41 affected SQL call ASTs, adapters and containing functions were
rechecked; the 875-call inventory preserves its reviewed classifications and
its original live-observation interval.

The Search dashboard, alert and acceptance queries now select maintenance.
The touched dashboard generator's pre-existing line-length violations were
fixed to satisfy changed-file lint. Its formatting preserves the exact AST and
all seven generated dashboards; only the intended Search selectors/descriptions
change their generated content.

[Local polling evidence](local-polling.json) proves that the maintenance poll
advances while the API's Search poll timestamp stays zero. Seven local worker
roles each have one fresh heartbeat. The first ownership probe found the old
host API/UI/scheduler no longer running after the earlier launcher replacement;
that failed prerequisite is retained. Those processes were restored in the
active checkout, using the same backing stack, before the passing probe. API
readiness and frontend return 200; the proxy is listening. No data was reset.

This is lifecycle/ownership evidence, not full feature QA. No indexes, live
embedding calls or fault injection were introduced by this probe. Container
image startup, full per-domain readiness, independent replica claim safety,
PG domain ports/durable jobs, agent/outbox separation and production deployment
remain open. SQL call identity risks already recorded in the risk register are
not closed by moving their process owner. Existing Search polling cadence is
unchanged. Exact delivered-commit CI remains required.
