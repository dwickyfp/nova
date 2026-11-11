# Foundation verification checkpoint

This is partial evidence on the active migration branch, not release acceptance.
No domain has switched its metadata authority to PostgreSQL. WP00 writer and
transform review, the migrator, domain ports, production Compose and cutover
remain open.

## Environment and source

- Baseline commit: `c6a51ff2e5a94a46191884ded40fc15394de27ed`.
- Branch: `feat/production-control-plane`, active original checkout.
- Application source digest at these checks:
  `489df16dc4f4b9fa20169642ea44d0a3273116ae87ce4e58d9478401073e72d9`.
  This is the ordered path/content digest produced by the inventory script,
  recorded before the foundation checkpoint commit.
- Existing local Docker project: `docker`, OrbStack/aarch64, eight CPUs and
  approximately 8 GiB Docker memory. Development data was not reseeded.
- One additional PG17 fixture service in that same project; PostgreSQL 17.11,
  image digest `sha256:665dc7b37ac52a17e298511414c21d23ffa4e6a6ef6225b3e5d59c7afb35e994`.
  Connections use verified TLS. Only fixture-owned synthetic schemas/roles are
  created and removed. This is not the production deployment.

## Local results on 2026-11-11

| Check | Observed result | Scope |
| --- | --- | --- |
| Backend unit and branch coverage | 10,415 passed; 65% | Complete final checkpoint unit suite, including API/metrics and scanner regressions (516.47 seconds) |
| Full application mypy | Passed | `uv run mypy app --no-error-summary` |
| Changed Python Ruff | Passed | 29 changed/new Python files; final checkpoint diff |
| Agent eval | 54/54 scenarios; 189/189 checks | `uv run python -m tests.eval.report`; controlled trajectories, not live-provider proof |
| Full agent trajectory suite | 347 passed | Controlled providers and governed tool fixtures; now also a required CI step |
| PG foundation and configuration | 25 passed | 11 real PG tests plus 14 configuration tests |
| Task recovery regressions | 79 passed | Worker, reconciler and worker-service unit tests |
| Bootstrap/process ownership | 28 passed | Lock failure refuses bootstrap; role startup duties preserved |
| Inventory scanner | 6 passed | Dynamic calls stay unresolved; injected/aliased adapters; review/evidence preservation |
| API authentication inventory | 1,223 passed | 611 session operations, each with missing and invalid bearer, plus explicit non-session allowlist |
| API inventory plus HTTP metrics regressions | 1,226 passed | Nested route prefixes, aliases and scrape exclusion; auth handler sentinels cover effective routes |
| Frontend lint/build | Passed | 0 lint errors, 31 warnings |
| Full frontend coverage | 1,593 passed across 192 files; line coverage 58.23% | Local browser files run serially, with the complete suite retained |
| Docs browser suite | 15 passed | `pnpm exec vitest run --browser.headless src/features/docs` |
| Changed docs in Chrome | Three pages verified | AI providers; Task management; multi-statement/Python Tasks. New role/recovery wording rendered; recovery section visually inspected |
| Real Redis role concurrency | Passed | 100 concurrent switches had unique revisions 2–101; deleted sessions were not recreated |
| Real StarRocks uncertain Task | Three attempts passed | Fixture-owned task/node stayed abandoned/running without an executor call; only its own rows were removed |
| PG fixture preparation | Passed | Generated TLS/config permissions 0700/0600; `docker compose config --quiet`; no extra container started |
| Live Console chat and saved history | Six of six attempts passed | Three independent calls each on cx/gpt-6-luna and cx/gpt-6.1-sol; structured synthetic output and persisted history verified; fixtures deleted |

The API auth inventory retains real FastAPI dependencies and replaces application
handlers with a failing sentinel, so a broken auth gate cannot mutate development
data. Token renewal is an explicit exception: its handler validates expired
signed tokens; invalid JWTs are additionally forbidden from reaching Redis.
It proves missing/invalid session refusal, not cross-user authorization or live
endpoint behavior. Internal service-token endpoints remain separately tested.

The PG checks cover actor/role RLS isolation, transaction-local settings, CAS
contention, rollback, schema/epoch refusal, unsafe DB privileges, statement
timeout, terminated connection without retry, diagnostic redaction, read-only
transactions, stale process fencing and a write freeze that waits for existing
transactions. They do not prove completed migrations, PgBouncer, HA, backups,
durable jobs or domain behavior on PG.

## Failures retained and follow-up

The first complete local run in this phase had 9,179 passes and three failures:
old provider/search-key fixtures assigned admin without activating that role.
Fixtures now activate the intended role, and negative inactive-admin cases were
added. Superset and Notebook fake Redis fixtures were also updated to execute
the actual atomic Lua path; assertions were retained.

The expanded full suite exposed an HTTP metric-label collision: FastAPI's
original included route lost its prefix. Metrics now resolve the effective
route context, with nested-prefix and alias regressions. The declared FastAPI
minimum matches the already locked 0.137.2 version whose routing API is used;
no FastAPI package version changed in the lockfile.

The first broader trajectory run had 340 passes and seven failures. Those
trajectories expected one SQL call despite the current semantic tool making one
answer query plus two bounded data-window probes. Updated assertions require
exactly those three calls, one ASC and one DESC probe, one-row probe limits and
the same principal/role/security version on every call. Existing consent,
model-call, provenance and answer assertions remain. The complete rerun passed
347 cases; this is controlled evidence, not live AI acceptance.

Two complete frontend attempts each had 1,592 passes and one failure: the mobile
drawer position was sampled during animation, then a lazily loaded News chart
had not rendered within the default polling deadline. The tests now await the
drawer's animations and give chart loading a bounded 10-second deadline while
retaining the geometry/chart-count assertions. The final complete serial run
passed. These failed attempts remain part of the evidence.

The existing FE container exited with code 255 at 09:08:33 UTC during local
checks; Docker reported OOMKilled=false. FE logs at 16:08:13–16:08:23 local time
record Berkeley DB DiskLimitException with 4.26–4.78 GB free against its 5.37 GB
minimum, immediately before the exit. A live inventory attempt failed while
it was unavailable. The same container and
volumes were started again, and existing host watchers reloaded after their
failed startup. Engine health and the API recovered. This unplanned outage is
not counted as a passed fault-injection or no-duplicate-effect test.

The previous docs commit's CI run `42977447467` completed with 240 integration
cases: 214 passed, two failed, 24 skipped. Both failures expected uncertain Task
nodes to remain abandoned; the worker had instead settled them as failed. The
worker now holds these runs, including when a sibling failed or a finalizer is
uncertain. Unit and local synthetic engine evidence pass. A new CI run has not
yet verified this revision. The previous CI result is not green.

Those 24 skips include live/custom tools, patched-FE/Ranger acceptance, engine
capabilities, cross-cluster migration, delayed-publication/label-expiry faults,
shared-data mode, governed Superset and observability. The ordinary engine lane
does not certify those capabilities. Required cases need appropriate disposable
CI fixtures; skips cannot be converted into a release PASS.

Live inventory confirms a configured chat provider with two LLMs and a configured
external-search key. Embedding tests were explicitly waived by the owner; no
embedding provider was configured for QA. Live chat, tools, MCP, AI SQL, Studio
and Dataviz acceptance after PG cutover remain pending. The separate
`LIVE_CHAT_CHECKPOINT.json` proves the six pre-cutover Console chat/history
attempts only. It does not certify tool calling, external search, Studio,
Dataviz, dependency failures or post-migration behavior.
