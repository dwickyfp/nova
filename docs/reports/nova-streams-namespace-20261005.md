# Nova Streams namespace implementation

## Delivery status

This change implements namespace contracts and persistence primitives. It does
not complete the approved namespace delivery or enable a production Stream.
PR #173 was already merged when implementation began. The worktree includes
the fetched `origin/main` through `0d0b4fa`.

`STREAMS_ENABLED` and `MANAGED_APPEND_ENABLED` default to false. Setting
`STREAMS_ENABLED=true` still returns provider unavailable. There is no production
provider that proves exclusive capture and governance equivalence, so the SQL
adapter does not admit CREATE or expose journal metadata.

## Implemented behavior

- `stream`, `database.stream`, and `database.default.stream` resolve to one
  immutable identity in the internal catalog. Database and object spelling are
  preserved; the UI placeholder `default` is case-insensitive. Other schemas
  and external catalog qualifications are rejected for managed Stream names.
- Names use the central ANTLR grammar, including quoted names, escaped backticks,
  and names passed as literals to `NOVA_STREAM_HAS_DATA`. A short source name
  uses the session database independently of the destination Stream's database.
- CREATE, DROP, DESCRIBE, SHOW STREAMS, STATUS, and BACKLOG produce typed ASTs
  and credential-free action payloads. Planning freezes the names without I/O.
  DROP declares destructive confirmation. `SHOW STREAM LOAD` remains native.
- The relation binder has an optional Stream schema adapter and records typed
  names, source spans, and aliases. Lexical CTE bindings take precedence. Native
  catalog-qualified table resolution is preserved.
- `CONFIG_STREAMS` stores immutable namespace generations. Creation, tombstones,
  and recreation use durable claims; recreation receives a new Stream ID. Exact
  readback can acknowledge a lost metadata response. An uncertain claim blocks
  publication; an old generation cannot replace a new identity.
- Runtime initialization, migration SQL, Docker initialization, and the existing
  seed path create the same tables. Metadata contains no data-plane payloads.
- Disabled SQL returns an explicit capability error through QueryService and
  the proxy. Refusals are audited using public SQL. HAS_DATA and deferred task
  calls are not forwarded to the native engine.

The catalog service accepts authorization and source-head adapters. Unit tests
exercise namespace/source denial, owner-access revocation, collision checks,
IF NOT EXISTS, and recreation with injected adapters. These tests do not prove
production authorization, source readiness, or consumption behavior.

## Validation

- `uv sync --locked`: passed.
- Focused namespace/catalog and QueryService/proxy refusal tests, including
  deferred tasks: 61 passed.
- Scoped SQL frontend and grammar regression suites: 142 passed.
- Eval scorecard: 48/48 scenarios, 165/165 checks.
- Grammar drift and user-data system-access checks: passed.
- Canonical grammar regeneration repeated with byte-identical artifacts.
- Changed Python Ruff: passed. Source diff whitespace checks passed; generated
  ANTLR files retain the generator's existing trailing-whitespace style.
- Seeded StarRocks 4.1.4 namespace gate: passed. It applies fresh and repeated
  migrations, races two independent repositories, checks database isolation,
  drops/recreates an identity, and rejects a stale publisher. Its disposable
  database rewrites only the metadata table prefix, leaving deployment metadata
  untouched.
- Full unit coverage suite after the execution-adapter fix: 6,661 passed,
  3 warnings, 59% coverage, 243.66 seconds. Subsequent whitespace-only cleanup
  and a consumer-test rename were also checked by Ruff and the focused suite.
- Full seeded integration: not passed. The run stopped progressing at
  `test_bootstrap_and_review_use_production_api_with_restricted_identity` and
  was interrupted after 444.42 seconds: 33 passed, 8 skipped, 30 deselected,
  exit 2. This is the same Studio live test that stalled the foundation work;
  its root cause remains unresolved. No assertions were disabled or weakened.
- Compose interpolation validation passed. The task's isolated containers were
  stopped after testing; other deployment containers were left running.

The isolated Compose project is `nova-streams-namespace`, with FE ports 39030,
38030, and 39408, MinIO 29000, and Redis 26379. The deployment's FE on port 29030
is not used by these tests.

## Remaining acceptance

- Production source registration, managed append, governance adapter, and
  authorized catalog execution remain unavailable. The new catalog service and
  relation-binding hook are not wired to a production provider.
- SELECT/INSERT snapshot execution, wildcard metadata handling, HAS_DATA
  evaluation, and consumption remain unfinished. Parser and mock-adapter tests
  must not be reported as successful end-to-end Stream queries.
- REST listing/detail/history and Explorer integration remain unfinished.
  No frontend files changed, and no frontend acceptance is claimed.
- Ranger acceptance and real caller revoke/policy tests remain unverified.
- A claim committed before its namespace row is published blocks the name.
  This implementation provides no blind retry, automatic repair, or forced
  namespace advance for that case.

Do not activate Streams or describe this change as production-ready. After the
failed integration gate and incomplete implementation were disclosed, the user
explicitly requested commit, push, and a PR. Publication proceeds under that
exception as a draft; it does not waive production acceptance or make the
failed integration run a pass. Generated test reports are excluded from Git.
