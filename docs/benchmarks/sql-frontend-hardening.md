# SQL frontend hardening acceptance

> Local acceptance of the twelve architecture gaps, tested on StarRocks 4.1.4.

## Architecture and scope

The production path remains parser → typed AST → semantic registry → planner
registry → bounded rules → execution plan → executor. QueryService owns request
lifecycle, script sequencing, audit and responses. The architecture and extension
instructions are in [arch-13](../arch-13-sql-frontend.md).

This change adds internal name mapping, schema decisions and transactional
composite execution. It does not add MERGE or ALL BY NAME syntax, automatic ALTER,
a physical optimizer, persistent-state migrations or cross-system compensation.
INSERT OVERWRITE retains its existing no-confirmation policy.

## Twelve-gap matrix

| Gap | Delivered behavior | Acceptance evidence |
| --- | --- | --- |
| 1. Engine capabilities | CURRENT_VERSION identity, release/build parsing, deployment discovery, configured overrides, bounded single-flight cache, startup warm and invalidation | Cold/warm, concurrency, TTL/retry, target/config isolation, invalid identity, failure and override unit tests; real 4.1.4 identity |
| 2. Confirmation authority | Typed policy for mutations; whole-script preflight before execution; executor enforcement; server-driven worksheet dialog | Zero-execution script refusal, confirmed CREATE then INSERT, overwrite exemption, proxy ERR refusal and browser cancel/keyboard/snapshot tests |
| 3. Statement semantics | Exact-type semantics registry; native grammar-context dispatch; task metadata effects exclude deferred body execution; declared rule bounds | Existing statement suites, literal/comment preservation and extension mutation with a SELECT-prefixed source |
| 4. Stage rewrite stability | Unique template slots; descriptors retained through rewrites; authorized runtime lowering; private sensitive-token slots | Multiple/scoped references, rewrite before/after stage, native FILES mixed with stages, ML preparation, EXPLAIN, COPY/LIST/export and live CSV regressions |
| 5. Relation output binding | Lazy relation binder for ordered projections, aliases, wildcards, joins, subqueries and CTE alias lists | Duplicate output preservation, outer-join nullability, stage metadata and structured rejection of undetermined shapes |
| 6. SQL type model | Raw engine types plus decimal/string/nested types; unknown future types; casting separate from safe widening | Nested ARRAY/MAP/STRUCT, widening/narrowing, unknowns and compatibility cases |
| 7. Bound metadata | Caller/role-scoped request cache; key, partition, generated, auto-increment and hidden flags where available; unknown otherwise | Lazy metadata tests, protected-column schema decisions and real caller-authorized catalog/DESC FILES probes |
| 8. Composite atomicity | Mandatory BEST_EFFORT or SINGLE_ENGINE_TRANSACTION; dedicated connection, pre-BEGIN eligibility, commit/rollback/unknown outcomes | Real shared-nothing INSERT commit/rollback, shared-data UPDATE/DELETE and repeated INSERT; cancellation, rollback failure and ambiguous COMMIT unit cases |
| 9. Execution plan payloads | Typed validated task, ML, security and password actions; private source retained only for execution material/audit | Consumer suites, credential-free plan enforcement and private-slot redaction regressions |
| 10. Action extensibility | Payload-type handler registration through existing adapters | New test mutation registers analyzer, validator, effects, planner, rule and handler and runs through unchanged QueryService routing |
| 11. Legacy helper dependency | Supported ANTLR/stage utility modules, compatibility imports and one shared implementation | Parser/consumer regressions, grammar drift and byte-identical parser regeneration |
| 12. Future DML readiness | Target-order name mapping, additive SchemaDelta candidates, combined read/write/delete/schema effects | Name mapping independent of ordinal; explicit ambiguity/missing/extra/type errors; unsafe/unknown metadata decisions; extensibility test |

All twelve gaps have implementations and acceptance coverage. This is evidence
for the internal contracts above, not acceptance of future MERGE/automatic schema
evolution features. Upstream MERGE capabilities remain false by default.

## Files and extension points

| Responsibility | Main files |
| --- | --- |
| Engine discovery and configuration | [capabilities/starrocks.py](../../backend/app/sql_frontend/capabilities/starrocks.py), [config.py](../../backend/app/core/config.py), startup in `app/main.py` |
| Semantics and script lifecycle | [analysis/semantics.py](../../backend/app/sql_frontend/analysis/semantics.py), [rules/registry.py](../../backend/app/sql_frontend/rules/registry.py), [query/service.py](../../backend/app/modules/query/service.py), query router/result schemas |
| Parser utilities and safe lowering | [antlr_utils.py](../../backend/app/sql_frontend/antlr_utils.py), [stage_parser.py](../../backend/app/sql_frontend/stage_parser.py), [stages.py](../../backend/app/sql_frontend/stages.py), [runtime_sql.py](../../backend/app/sql_frontend/runtime_sql.py), preparation and execution adapters |
| Binding and schema decisions | [binding/relations.py](../../backend/app/sql_frontend/binding/relations.py), [binding/types.py](../../backend/app/sql_frontend/binding/types.py), [binding/schema.py](../../backend/app/sql_frontend/binding/schema.py), catalog/models/StarRocks metadata |
| Plans, handlers and atomicity | Planning payloads/execution/planner; [execution/executor.py](../../backend/app/sql_frontend/execution/executor.py), [execution/transactions.py](../../backend/app/sql_frontend/execution/transactions.py), [planning/transactions.py](../../backend/app/sql_frontend/planning/transactions.py) |
| Worksheet confirmation | [query-confirmation.tsx](../../frontend/src/features/workspaces/query-confirmation.tsx), workspace request lifecycle and response types |
| Acceptance fixtures | Unit hardening/readiness and consumer regressions; shared-data compose; live transaction/Ranger tests; seed and Ranger verification scripts; benchmark harness |

## Validation

The shared development checkout contained concurrent changes to Agents,
Intelligence, ML and other features. Validation used a detached checkout at
`6e3cb0dd73e9b107180ecad351bfc43af9fb85d6` with only this goal's changes applied.
Those unrelated changes are excluded from this delivery. No production Docker
containers or volumes were used for acceptance.

Python 3.11 used the locked backend environment. Frontend setup used Node
22.22.3, pnpm 11.8.0, a fresh frozen-lockfile install and Playwright Chromium.
The final code snapshot passed the following local checks on 2026-10-01:

| Check | Passed | Failed | Skipped | Notes |
| --- | ---: | ---: | ---: | --- |
| Full backend unit/coverage | 4,653 | 0 | 0 | `pytest tests/unit --cov=app --cov-branch`; coverage 53%, three warnings |
| Focused hardening/readiness | 69 | 0 | 0 | Included in the unit total; not an additional unique test count |
| Assistant eval | 48 scenarios / 165 checks | 0 | 0 | `python -m tests.eval.report` |
| Seeded real-engine integration | 162 | 0 | 6 | `pytest tests/integration -m engine`; 29 tests outside the marker deselected; 479.896 seconds |
| Patched-FE Ranger HTTP acceptance | 1 | 0 | 0 | Separate isolated Ranger fixture; retains protected ACCOUNTADMIN HTTP 403 assertion |
| Patched-FE Ranger policy acceptance | Complete CLI scenario | 0 | 0 | Regional row filters, masks, role changes, concurrent caller isolation and assistant caller scope |
| Frontend coverage/browser suite | 945 in 120 files | 0 | 0 | `pnpm test:coverage --maxWorkers=1`; lines 46.41%, statements 45.75%; 69.01 seconds |
| Changed-file Ruff | 51 Python files | 0 | 0 | Blocking check clean |
| Grammar drift/regeneration | Both checks | 0 | 0 | Pin checked; generated artifacts byte-identical |
| User-data system-access checker | Complete check | 0 | 0 | Caller data access boundary retained |
| Frontend lint/build | Both checks | 0 | 0 | Lint: zero errors, 24 warnings; build passed |

Full-tree report-only Ruff reported 175 findings; mypy reported 241 errors. There
were no mypy errors in `app/sql_frontend`. Findings on other changed goal paths
were missing third-party yaml/asyncmy type information. These report-only checks
are not represented as clean gates.

The six integration skips were: opt-in custom SQL live tools, opt-in live LLM
provider, disabled engine UDF support, unsupported native CREATE TASK, missing
second-cluster migration fixture, and the Ranger-specific test awaiting its
separate fixture. The last test subsequently passed against patched FE/Ranger.
Thus five collected integration cases remained not runnable in these fixtures.
They are existing optional acceptance paths, not proof for those features.

Earlier attempts are excluded from the passing totals: the shared checkout had
two failures caused by concurrent unrelated provider changes; an engine rerun
lost its FE to OOM and produced mostly skips. After freeing memory and restarting
only the owned FE, the final engine suite above ran successfully. Parallel
frontend coverage attempts had hover/animation failures under load, including a
serial attempt overlapping build/preview work; the complete isolated
serial run passed without removing tests, weakening assertions or adding skips.
The result does not establish that the parallel suite is stable on this host.

These are local checks. GitHub Actions results are not asserted.

Additional Playwright inspection ran the actual worksheet route with simulated
API responses at 1280×720 and 320×640 in light and dark themes. It checked server
refusal, cancel without a second request, keyboard confirmation and the captured
SQL/tab/database/schema payload. The open desktop assistant initially exposed a
mobile-sheet stacking issue on resize. The delivered dialog closes that sheet
while confirmation is pending; a browser regression uses the real AssistantPanel
and confirms that the dialog remains reachable after the viewport change. This
UI inspection complements the real HTTP/engine tests; its API was simulated.

## Performance

The CPU comparison ran the same six-statement corpus and benchmark harness
against the pre-change frontend at the revision above and the delivered frontend.
Each path had 300 warm samples. It excludes catalog binding, capability detection,
secret resolution, audit and engine I/O.

| Warm parse/build/plan path | Before median / p95 | After median / p95 |
| --- | --- | --- |
| Six-statement corpus | 351.27 / 2,024.12 µs | 290.40 / 613.38 µs |
| SELECT 1 | 220.58 / 895.04 µs | 198.15 / 286.17 µs |

These are single-host measurements under variable load, not a performance SLA or
a statistically established speedup. The legacy compatibility parser's cheap
native short circuit is not used as a before/after architecture baseline.
The harness is `backend/tests/benchmark/sql_frontend_report.py`.

A separate production QueryService SELECT 1 probe used 50 measured calls after
five warmups against the isolated shared-nothing engine, including caller role
setup, statement execution and audit. Median was 105.517 ms and p95 267.336 ms.
It opened 50 user connections, made 50 system-pool audit checkouts and observed
150 SQL commands: SET ROLE, SELECT 1 and audit insertion per request. Driver
handshake/session initialization is outside that command count. There were zero
catalog binding calls, role-discovery queries or capability network probes after
warmup. A separate ten-request regression asserts one initial capability probe
and zero binding requests. The live measurement overlapped other acceptance
work on the host and has no comparable live pre-change baseline.

## Security and readiness limits

Plans and diagnostics carry no credential-bearing SQL. Private native material
is restored only at execution; every selected stage is authorized before storage
secrets are resolved. Error results, HTTP responses, execution failure metadata
and audits preserve redaction. Protected-role/user/UDF and egress guards remain
pre-parse controls, separate from typed confirmation policy.

The real Ranger checks exercised Alice/Bob identities, regional filters, masked
versus raw role access and assistant access without substituting an administrative
principal for caller data access. Transaction connections use caller credentials
and set role/database once before BEGIN. They do not borrow a proxy transaction.

Unknown deployment or transaction dependencies cause refusal. UPDATE/DELETE and
repeated INSERT require proven shared-data support; operators cannot override
structural restrictions. Lost COMMIT responses and failed rollback report an
unknown outcome and discard the connection rather than retrying.

Relation binding deliberately rejects recursive CTE, set-operation and ambiguous
USING/semi/anti-join output forms when their output cannot be established. Complex
aliased expressions retain UnknownType. Missing metadata prevents a safe schema
decision. SchemaDelta is a decision model only: no ALTER or schema migration is
executed. Future public syntax still requires its own grammar, planner and live
acceptance work.
