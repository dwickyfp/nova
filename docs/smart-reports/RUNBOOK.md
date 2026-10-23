# Demo, migration and recovery runbook

Use TEST_MATRIX.md and STATUS.md for recorded results; this runbook describes
repeatable operation and recovery.

## Demo acceptance

1. Sign in with a named active role and an authorized published semantic source
   containing cities Jakarta and Bandung.
2. In Studio, request a report with a revenue KPI, city table and city filter.
   Confirm the returned report is a private draft and its content block carries
   references rather than persisted result rows.
3. Select Jakarta in chat and Open Full. Verify the filter and ViewSession ID match.
   Confirm the handoff token disappears from the URL.
4. Change the filter to Bandung, then Return to conversation. Verify the chat report
   updates. Ask Nova about the selection and inspect server evidence for Bandung.
5. Edit a widget/title in the draft. In an independent session open the published
   report and verify it is unchanged. Publish explicitly, then verify a new immutable
   version appears. Attempt a stale publish and expect 409.
6. Copy the canonical link. Confirm its session is independent and the recipient
   needs both report access and data access. Switch roles while a query is pending;
   confirm old data is cleared and its late response is rejected.

## Existing-stack verification

Before container-dependent checks, inspect `docker compose ls` and `docker ps`.
Read fixture setup/teardown. For the existing disposable integration project use:

```sh
cd backend
export COMPOSE_PROJECT_NAME=nova-smart-unlimited
export NOVA_TEST_FE_MYSQL_PORT=39030
export NOVA_TEST_FE_HTTP_PORT=38030
export NOVA_TEST_FE_ARROW_PORT=39408
export NOVA_TEST_REDIS_PORT=36379
export NOVA_TEST_MINIO_PORT=39000
uv run python -c 'from tests.conftest import _stack_already_reachable; assert _stack_already_reachable()'
uv run pytest tests/integration/test_smart_report_engine.py -v --tb=short
```

Do not run the fixture if the endpoint assertion fails. Do not create a replacement
stack or reset development volumes. Use the patched `docker` project for governed
acceptance after inspecting its own test prerequisites; the stock integration FE
does not prove Ranger behavior.

The local Ranger identity and missing Compose override were repaired with the
operator's authorization. [LOCAL_RANGER.md](LOCAL_RANGER.md) records the local file
paths and complete Compose command. Use `backend/.env.ranger.local` for governed
acceptance; never print its contents. The report acceptance now passes on the
patched stack. Do not reset development data or treat stock-engine checks as
evidence of Ranger enforcement.

## Migration

Application startup ensures the additive report journal then runs the restartable
dashboard metadata migration. It is idempotent by dashboard identity and source
fingerprint. Inspect migration records against the complete legacy dashboard
inventory before declaring the migration finished. Source dashboards and shares
remain accessible. Mapping validation must run as an authorized owner/editor.

Never run legacy artifact SQL with system credentials to discover its semantics.
Missing mappings stay `Requires mapping`; publish remains blocked. Source changes
must be reviewed against the migrated draft and must not overwrite user edits.

## Failure recovery

- 409 on state: fetch the current ViewSession, then submit a new event with its
  expected revision. Reuse an event ID only for the same request.
- Expired/replayed handoff: open the report again from its current conversation.
  Do not extend or reuse a consumed token.
- Redis outage: fail closed for report coordination. Do not substitute process
  memory for durable revisions or another principal for data execution.
- Publish timeout: retry the original operation ID and unchanged request. If a
  checksum/fork conflict remains, preserve all journal rows and inspect the original
  version/pointer operation. Do not delete a competing revision or use timestamps
  to resolve ownership.
- Application rollback: retain `CONFIG_SMART_REPORT_EVENTS`, managed payloads and
  legacy sources. No feature flag or destructive reverse migration is provided.

Final delivery requires the full repository gates in AGENTS.md, including affected
agent trajectories, full backend/frontend checks, user-data boundary checks, seeded
integration and patched-FE/Ranger acceptance. Missing prerequisites are blockers,
not skipped passes.

## Exports and schedules

Use More actions for export, template and Activity & schedules. Publish before
scheduling. The subscriber's active role must already have an explicitly configured
execution user matching that subscriber; a report grant alone does not establish
this binding. Configure the existing worker credential/impersonation mechanism,
then run the existing task worker against the same engine and Redis.

A report version change suspends successful execution until the subscriber reviews
and resaves the schedule. A stale prepared activity becomes cancelled; retry with
a later scheduled execution ID. Never reuse a prior operation ID with changed input.
For a timeout, retry the same ID/request first. Inspect the report journal and task
history; do not delete an ambiguous revision to force recovery.

Exports require the frontend production build, Node and Playwright Chromium. The
backend image packages these dependencies. Locally run the pinned frontend build
and Playwright browser installation. Snapshot failures return an explicit error;
there is no spreadsheet-only fallback for a requested PDF/PPTX. Every report page
is included, and long pages may occupy several PDF pages or presentation slides.

`REPORT_EXPORT_MAX_CONCURRENCY` is a positive per-process workload budget, default
2. It limits export encoding/Chromium through the existing query-admission owner
and also respects the global query limit. Excess exports return audited HTTP 429
with `Retry-After: 1`; they do not create a manifest or start another renderer.
Retry the same operation after capacity is available. Cancellation terminates the
renderer process group and releases its slot. This budget cannot disable exports.

Published grants, role binding and source access are checked again at execution
and download. A changed evidence fingerprint prevents release of a historical
file/result; create a fresh export or run after reviewing access. Object payloads
are private and are never linked directly from the browser.

## UI judge rerun

Run test_smart_report_browser.py against the existing governed stack, then review
its screenshots and downloaded files. Run test_smart_report_live_model.py with
NOVA_REPORT_LLM_ACCEPTANCE=1 only for an intentional external provider test. That
switch controls the test, not product availability. The scripted stream test and
real-provider plan test establish different behaviors; retain both classifications.

From `backend/`, after checking the existing governed endpoints:

```sh
uv run --env-file .env.ranger.local pytest \
  tests/integration/test_smart_report_ranger.py \
  tests/integration/test_smart_report_stream.py \
  tests/integration/test_smart_report_browser.py -v --tb=short
NOVA_REPORT_LLM_ACCEPTANCE=1 uv run --env-file .env.ranger.local pytest \
  tests/integration/test_smart_report_live_model.py -v --tb=short
```

The browser fixture creates a temporary HTTP listener and Vite server, uses the
existing governed engine, and cleans up its own report/user/policy data. Screenshot
and download evidence remains in pytest's temporary directory. Inspect the images
before copying selected evidence into this documentation.

For the full frontend coverage gate on a memory-constrained workstation, run
`corepack pnpm@11.8.0 --dir frontend test:coverage --maxWorkers=2` from the repository
root. This limits parallel workers without changing the collected suite or assertions.
Run broad backend, frontend and image-build jobs sequentially on this local stack.

## Local disk recovery

The 2026-10-23 image build temporarily reduced free space below StarRocks JE's
5 GiB journal reserve. Both existing FE processes exited with `DiskLimitException`.
Only this task's obsolete image, identified build-cache records and temporary
package cache were removed. The same FE containers were started again with their
original data; the reserve and volumes were not changed.

Keep at least 10 GiB free before a cold image build plus broad acceptance. Check
host and container free space, not just Docker health. If the journal refuses
writes, stop acceptance, recover disk space using explicitly owned build outputs,
restart the same stopped FE containers, verify readiness, and rerun affected tests.
Do not lower the journal reserve, prune unrelated images, or reset metadata.

## Hosted integration prerequisites

The L3 workflow starts the disposable stock test engine with
`backend/docker-compose.test-ranger.yml`, which inherits the existing Ranger
services from the governed Compose definition. Authentication, service bootstrap
and a readable policy revision are checked before pytest. The workflow generates
its own throwaway credentials and uploads `backend/integration.xml` on failure.
This validates application integration with a real Ranger control plane; FE row
filters and masks still require the separate patched-FE acceptance environment.

Local reruns use the existing disposable engine and existing Ranger endpoint from
`backend/.env.ranger.local`. Do not start the CI overlay beside those running
services or copy CI teardown commands into local validation.
