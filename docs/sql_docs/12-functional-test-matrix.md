# Functional SQL test matrix

The Python suite in `backend/tests/integration/functional_sql/` executes SQL through
Nova's public MySQL proxy. It checks values, result types, refusals, fixture effects,
and cleanup. A generated recipe is a candidate test; it becomes evidence only after
execution and a passing oracle.

The suite is still being verified. The full matrix has not passed. Do not infer
complete SQL support from the inventory, generated cases, or a passing subset.

## Inventory and sources

The captured local inventory contains 820 function names, 6,242 distinct
name/argument/return/kind catalog entries, and 264 grammar statement alternatives.
`baseline.json` preserves their identities. Pagination uses 400 rows per request
because the public proxy caps result sets at 500 rows.

The engine source pin is
[`4a9848edf03f5c936dac664b2d52527f48e72eb0`](https://github.com/StarRocks/starrocks/tree/4a9848edf03f5c936dac664b2d52527f48e72eb0).
Function inputs come from the
[scalar registry](https://github.com/StarRocks/starrocks/blob/4a9848edf03f5c936dac664b2d52527f48e72eb0/gensrc/script/functions.py)
and [FE function registry](https://github.com/StarRocks/starrocks/blob/4a9848edf03f5c936dac664b2d52527f48e72eb0/fe/fe-core/src/main/java/com/starrocks/catalog/FunctionSet.java),
checked against runtime `SHOW FULL BUILTIN FUNCTIONS`. Nova statement inventory
comes from `backend/app/sql_dialect/grammar/StarRocks.g4`.

This is a lossy inventory. The pinned
[Function.getSignature/getInfo](https://github.com/StarRocks/starrocks/blob/4a9848edf03f5c936dac664b2d52527f48e72eb0/fe/fe-core/src/main/java/com/starrocks/catalog/Function.java)
renders primitive types, so ARRAY and aggregate-state descriptors can appear as
`INVALID_TYPE`. The
[SHOW executor](https://github.com/StarRocks/starrocks/blob/4a9848edf03f5c936dac664b2d52527f48e72eb0/fe/fe-core/src/main/java/com/starrocks/qe/ShowExecutor.java)
then deduplicates that rendered signature. For example, the scalar source registry
contains 15 `array_min` overloads, while the captured catalog exposes one entry.
Generic complex-type recipes therefore do not count as concrete signature coverage.
`scalar_registry.json` records 963 scalar overloads extracted from that exact source,
with function IDs, logical arguments, return types, source link, and source checksum.
`engine_registry.json` additionally preserves 7,522 concrete definitions from the
pinned FE, including recursive ARRAY types and each aggregate-state descriptor's
original arguments. `DumpFunctions.java` reflects only builtin definitions;
the Python extractor runs in an explicitly selected test Compose container with
64 MiB for compilation and 128 MiB for inspection. It records source, inspector,
and FE jar checksums. Repeated extraction reproduced the same 7,522 definitions.
This inventory does not execute user queries or replace tests through Nova.

Typed recipes have separate scalar-source and engine-definition identities and
coverage counts. Reports distinguish successful refusals from positive engine
overload results. Missing recipes remain blocked until they are executed and
verified.
Integer-return definitions with the same name and arguments also check the native
MySQL column type. Equal numeric values cannot prove both return widths. The pinned
function matcher compares argument types without comparing the return type, so
these collisions require binding evidence before both definitions can count.
`classification.py` records 13 scalar definitions that are shadowed by an earlier
definition with identical arguments: seven old date/time extractors, four 32-bit
`unix_timestamp` definitions, and two deprecated 32-bit `get_json_int` definitions.
The exclusions match function ID, signature, kind, and return type; each points to
the registration order and matcher source. They remain in the manifest as
`EXCLUDED` and never count as successful execution. The selected public variants
still require positive tests and native column-type checks. Other internal or
ambiguous definitions remain blocked until researched.
Operator recipes also compare the return type with the registered definition.
The pinned analyzer promotes operands for arithmetic; for example, two INT
operands in `+` produce BIGINT. A correct value with a different return type is
recorded as `BLOCKED`, with its rows and metadata preserved, until the intended
definition has a verified public binding path. It does not count as positive
overload coverage. See
[ExpressionAnalyzer.getArithmeticFunction](https://github.com/StarRocks/starrocks/blob/4a9848edf03f5c936dac664b2d52527f48e72eb0/fe/fe-core/src/main/java/com/starrocks/sql/analyzer/ExpressionAnalyzer.java#L1992).
Six hidden TINYINT/FLOAT operator definitions have sourced exclusions because
the analyzer always promotes those operands before lookup. SMALLINT/INT recipes
use the preceding input width to bind the declared operator, and the BOOLEAN
operator path uses two NULL operands as specified by that analyzer. These
classifications preserve the inventory and never inflate positive counts.
The public `mod(FLOAT,FLOAT)` function is tested as a function call; infix `%`
would promote its operands and test another definition.

To reproduce the concrete inventory with the pinned `FunctionSet.java` source:

```bash
uv run python -m tests.integration.functional_sql.extract_registry \
  --container <isolated-test-fe-container> \
  --function-set-source <pinned-FunctionSet.java> \
  --output /tmp/engine-registry.json
```

The extractor rejects a source checksum or FE jars that differ from the recorded
pin. Registry upgrades require research against the new engine before changing
those checksums.

The manifest records every inventory identity, even when no executable case exists.
Each entry carries its source, cases, SQL, setup, expected result, oracle, and
prerequisite. Unmapped entries remain blocked. Internal names and pseudo-types
require an explicit supported user path or a sourced exclusion; their presence in
`SHOW FUNCTIONS` alone does not establish a callable public contract.

## Required coverage

| Group | Evidence required |
| --- | --- |
| Scalar | Typed overloads for numeric, string, Unicode, date, timezone, binary, hash, encryption, regex, geospatial, utility, and conditional functions |
| Geospatial | All 15 pinned ST_* definitions and aliases; stored points, lines/polygons, holes, circle/polygon filters and joins, nearest locations, spherical distances, coordinates, NULL/invalid inputs, binary transport, and empty-result metadata |
| Complex types | ARRAY/lambda, MAP, STRUCT, JSON, VARIANT, metric constructors, access, conversion, and MySQL serialization |
| Aggregate | Supported typed inputs, DISTINCT, bitmap/HLL/percentile/statistics/funnel/retention, state construction and merge/union/combine |
| Analytic | Window partition/order/frame, ranks, offsets, first/last values, grouping sets, rollup, and cube |
| Query | Joins, subqueries, CTEs, set operations, filtering, ordering, limits, quotes, comments, literals, and real client parameter binding |
| DDL/DML | Database/table key models, CTAS/LIKE, views/MVs/indexes/partitions, generated columns, insert/overwrite/update/delete, alter/drop/truncate, and verified effects |
| Stage | File schemas, CSV/Parquet/ORC, directory/glob, LIST, COPY, export, multi-stage joins, denied access, and all file/metadata cleanup |
| Nova SQL | Task metadata plus actual execution, supported ML types, SQL prediction/forecast, API pairs where required, and relay materialization limits |
| AI | Seven configured live aliases, `ai_query` contract, semantic/schema criteria, secret redaction, and a shared cost ceiling |
| Session/protocol | Login, role/catalog/database, user/system variables, charset, result metadata/NULL, scripts, limits, ping, timeouts, disconnect, and actual mysql:8.0 |
| Governance | Active-role enforcement, denied access, row filtering, masking, grants/revocations, protected ACCOUNTADMIN, redacted audit, and cache/session isolation |
| Operations | External catalogs, load/export/pipe, SQL UDFs, statistics, profiles/EXPLAIN, advisor/baseline, dictionary, resources, backup/restore with live prerequisites |

Every supported public signature needs a positive case. Additional cases cover
NULL, empty inputs, type limits, overflow, decimal precision, invalid arguments,
missing objects, dependency failures, and denied permissions. Aggregate states
must be constructed by their owning functions. Casting an ordinary scalar into a
state is not evidence.

## Execution

Run from `backend/` with the repository's Python 3.11 environment:

```bash
uv sync --locked
uv run python -m tests.integration.functional_sql.run --target local --suite all --strict
```

Provide `NOVA_SQL_TEST_PASSWORD` through a private environment or use the
interactive password prompt. The runner never places the password in process
arguments. The real mysql:8.0 client reads a temporary private option file.

Useful selections:

```bash
uv run python -m tests.integration.functional_sql.run --inventory --strict
uv run python -m tests.integration.functional_sql.run --suite core --workers 1 --strict
uv run python -m tests.integration.functional_sql.run --suite scalar,complex --workers 1 --strict
uv run python -m tests.integration.functional_sql.run --suite conditional,analytic,table --workers 1 --strict
uv run python -m tests.integration.functional_sql.run --suite geospatial --strict
uv run python -m tests.integration.functional_sql.run --suite geospatial,protocol --strict
uv run python -m tests.integration.functional_sql.run --suite stage --strict
uv run python -m tests.integration.functional_sql.run --suite ai --configure-ai --strict
uv run python -m tests.integration.functional_sql.run --suite all --rerun /path/to/report.json --strict
```

`--case` selects a case ID glob and can be repeated to select multiple cases. `--report-dir` selects an artifact directory.
For the user-authorized security exclusion in this development run, append
`--exclude-group security --exclusion-reason "User requested skipping security cases"`.
The runner preserves these cases as `USER_EXCLUDED` in `excluded_cases.json` and
the manifest. They never count as passed tests or positive coverage.

`--host`, `--port`, `--api-url`, and `--user` select explicit endpoints and a caller.
`--role` defaults to `ACCOUNTADMIN`; both clients explicitly activate that role.
Use a role assigned to the tested principal for restricted-access cases.
ML fixture training defaults to Nova's `balanced` mode. `--ml-mode` selects
`interactive`, `balanced`, or `best`; the chosen mode and SQL appear in the report.
Mode-specific deadlines still apply. A timeout remains a blocked prerequisite,
including when another mode can train successfully.
The pytest adapter can run the same matrix:

```bash
NOVA_SQL_TEST_SUITE=core uv run pytest tests/integration/functional_sql/test_acceptance.py -v
```

The adapter requires `NOVA_SQL_TEST_PORT`, `NOVA_SQL_TEST_API_URL`, and
`NOVA_SQL_TEST_PASSWORD`. Its `functional_sql` marker keeps it separate from the
ordinary seeded `engine` CI selection. Missing explicit configuration fails.

All outcomes must pass for exit zero. `--strict` documents that intent; omitting it
does not weaken failure handling. Unknown groups and empty selections fail.
Inventory drift also blocks the run. Update the baseline only after researching
and reviewing the changed signatures and grammar alternatives.

## Fixtures and boundaries

Each run creates its own `nova_sql_test_*` database and deterministic data. Stage
fixtures use unique names and prefixes. Provisioning and confirmation use Nova's
existing API, while primary query evidence comes from the public MySQL path.
Cleanup checks database absence and removes uploaded files before stage metadata.
A private `fixtures.json` journal records each provisioning attempt before dependent
work. An interrupted run can recover its owned objects with:

```bash
uv run python -m tests.integration.functional_sql.run --cleanup-fixtures /path/to/fixtures.json
```

Recovery verifies the target, API endpoint, principal, and fixture name/scope
before removing objects. Its report preserves the original run's failed outcomes.
Task tests check metadata and sink data, native task effects, and isolated worker
completion. ML tests train the five supported kinds through the authenticated API
and verify predictions and forecasts through MySQL. Training evidence is retained
per model when a later dependency fails. Task/model/file cleanup precedes database
cleanup.
A failed cleanup is part of the report and prevents a successful run.

State combinator trials require `--isolated-stack`, a separate proxy port, and a
separate API endpoint. The runner rejects the active local endpoints for this
mode. Restart/fault/shared-data/global trials also belong in disposable stacks.
Never seed the active governed Nova deployment or remove another checkout's
containers or volumes.

Proxy transactions and unsupported session changes must return explicit errors.
Supported system settings execute on the caller connection. Mixed scripts that
cannot preserve real session semantics are refused before effects. Bounded ML
prediction and API materialization retain their existing relay contract.
User-variable expressions are evaluated once through the caller's SQL path;
later reads use the saved value. Assignment failures preserve the previous value.
Model deletion verifies physical artifact absence before removing registry
identity; failed storage cleanup leaves metadata available for retry.

## AI spending

The approved configuration uses Kenari `deepseek-v4-1-flash` for the seven aliases.
`--configure-ai` persists these aliases through Nova's existing configuration API.
The test first verifies redaction with a synthetic canary. A failed redaction check
blocks live provider work.

The shared ledger defaults to `/tmp/nova-functional-sql/ai-budget.json`. It reserves
a conservative per-call upper bound before live execution, including token
headroom and retries, and refuses reservations above US$10. Reports distinguish
this upper bound from actual billed usage, which SQL responses do not expose.
Preserve the ledger across runs; deleting it would reset the shared budget.

## Reports and interpretation

The runner writes `inventory.json`, `cases.json`, `excluded_cases.json`, `manifest.json`,
`fixtures.json`, `report.json`,
`junit.xml`, and `report.html`. JSON preserves observed rows and column metadata,
SQL reproductions, timings, coverage identities, cost reservation, and cleanup
evidence. Failed effect checks retain their observed values. A MySQL timeout
closes the connection before another query can consume its delayed response.
HTML escapes SQL/data. JUnit records blocked cases as errors with zero
skips.
Reports include hashes of application source, harness, and repository configuration
files before and after execution. Changing these files during a run blocks the
result and requires rerunning. These hashes describe disk contents; the test
operator must also start the API/proxy from the same code before acceptance.

`function_signatures_positive` counts passing value/effect cases.
`function_signatures_refusal` counts passing expected-refusal cases separately.
The union, `function_signatures_tested`, measures tested contracts and does not
mean every listed overload executed successfully. A subset's PASS applies only to
that selection. Full `all` runs add blocked outcomes for every unmapped function
or statement. `scalar_logical_overloads_tested` counts passing typed recipes from
the source registry independently of the lossy catalog entries.

Examples of source-backed runtime boundaries:

- The pinned FILES implementation accepts CSV, Parquet, ORC, and Avro; it refuses
  JSON. The stage JSON case therefore verifies that refusal. Avro still needs a
  fixture before it can be claimed as tested. See
  [TableFunctionTable](https://github.com/StarRocks/starrocks/blob/4a9848edf03f5c936dac664b2d52527f48e72eb0/fe/fe-core/src/main/java/com/starrocks/catalog/TableFunctionTable.java).
- The pinned analytic analyzer limits bitmap/HLL window paths and refuses
  percentile window inputs. Catalog entries for these combinations are not
  sufficient positive evidence. See
  [AnalyticAnalyzer](https://github.com/StarRocks/starrocks/blob/4a9848edf03f5c936dac664b2d52527f48e72eb0/fe/fe-core/src/main/java/com/starrocks/sql/analyzer/AnalyticAnalyzer.java).

The current investigation also reproduced a BE SIGSEGV in
`ds_hll_accumulate` when the planner lowered a constant input into an HLL state
function. This public aggregate and its state paths require the isolated stack;
they remain blocked on the primary stack. The interrupted full run selected
12,757 cases and reached 1,053 before interruption; its 424 PASS, 258 FAIL and
383 BLOCKED outcomes include errors after backend loss. Its cleanup initially
failed, and a separate recovery verified all four fixture groups afterward.
Those results are diagnostic evidence, not a completed full-suite result.

The subsequent full run included geospatial and selected 12,810 cases. Its
report contains 5,019 PASS, 368 FAIL and 9,768 BLOCKED outcomes, including
unmapped coverage and outcomes after backend loss. The BE exited with SIGSEGV
while a decimal-key `map_agg` case was running. The crash stack identifies
tablet-writer serialization; it does not establish which query caused the
failure. The BE restarted with the existing allocation. A separate recovery
verified task, model, stage and database cleanup. The original failed report
remains unchanged. The runner now blocks client compatibility work when its
engine health probe fails.

HLL configuration recipes now pass literal precision and target values, as
required by the pinned analyzer. Their rerun produced 190 PASS and four FAIL
outcomes with verified cleanup. The remaining failures are the two- and
three-argument `ds_hll_count_distinct` DECIMALV2 contracts, recorded separately
in catalog and concrete-definition cases. The engine reports an index error;
these failures remain open.

The current investigation also reproduced malformed DATE state output for
`any_value_state`, and a state merge caused a BE process failure. Keep that
reproduction confined to disposable engines. It remains a failure until a
validated engine fix establishes the correct value and type; it is not an
expected-refusal case.

## Geospatial data and transport

The geospatial selection uses six locations: Jakarta, Bandung, Surabaya, the
origin, and both sides of the antimeridian. It persists native geometry in
VARCHAR columns, verifies coordinates after storage, checks circle filters and
polygon joins, and calculates city distances and nearest-location ordering.
The Python distance oracle uses the haversine formula and the radius from the
[pinned S2 0.9.0 library](https://github.com/google/s2geometry/blob/v0.9.0/src/s2/s2earth.h).
The engine calls S2 for spherical distance in meters; this is not an ellipsoidal
or altitude-aware calculation. See the pinned
[geometry implementation](https://github.com/StarRocks/starrocks/blob/4a9848edf03f5c936dac664b2d52527f48e72eb0/be/src/geo/geo_types.cpp).

Point, line and polygon aliases have positive cases plus NULL, malformed WKT,
wrong shape, coordinate bounds and polygon-hole checks. The pinned WKT grammar
supports point, line and polygon; unsupported EMPTY/Z/multi/collection forms
return NULL and are tested as documented boundaries. A negative circle radius
produces S2's valid empty cap, whose containment result is false.

The live test found that a raw `ST_Point(0,0)` result failed UTF-8 decoding in the
MySQL relay. The query adapter now preserves opaque bytes for relayed sessions,
decodes ordinary text, retains numeric/date/time converters, and restores the
driver's decoding settings after the cursor drains. The proxy advertises binary
charset for byte values while retaining the engine's VARCHAR type. The raw point
and parameter roundtrip pass; the mysql:8.0 harness uses `--binary-as-hex` to keep
binary output intact in text reports. This change concerns relayed MySQL results;
API callers should use `ST_AsText` for readable geometry.

## Completion gates

Complete coverage means the mandatory supported matrix and documented exclusions
have been reviewed, every required live case passed, and cleanup and Nova health
were verified. It does not guarantee every possible SQL/input combination.

Before delivery, run the full backend unit coverage suite, eval scorecard,
changed-file Ruff, seeded real-engine integration with all isolated port overrides,
and patched-FE/Ranger acceptance. Grammar edits additionally need drift and
regeneration checks. Required checks must cover the final code after remote-main
integration. Missing dependencies, engine crashes, incomplete coverage, failed
checks, or failed cleanup block commit/push under the repository's completion
rules.

## Development verification, 2026-10-04

These results apply to the local checkout and selected cases. They are not a
GitHub Actions result or evidence that the full mandatory matrix has passed.

| Check | Observed result |
| --- | --- |
| Full backend unit suite after the geospatial and harness fixes | 5,403 passed; zero skips/failures; application coverage 55% with branch measurement enabled |
| Agent eval scorecard | 48/48 scenarios, 165/165 checks passed |
| Ruff on changed Python | 74 files passed |
| User-data/system-access boundary checker | Passed |
| Patched-FE Ranger acceptance | Proxy roles, row filters, masks, session isolation, root refusal, and assistant SQL passed |
| Governed ML and stage acceptance | 34 cases passed, with five real trained model kinds, MySQL predictions/forecast, stage reads, mysql:8.0, and verified cleanup |
| ARRAY `null_or_empty` regression | Empty, nonempty, NULL-array, and NULL-element behavior passed |
| Typed scalar/complex/conditional/window/table selection | 1,112 PASS, 8 BLOCKED, zero FAIL; 1,098 positive engine definitions and 12 verified refusal contracts; cleanup confirmed |
| Live AI | Seven configured aliases exercised; cumulative conservative reservation US$0.067584 including the current full run, against the US$10 limit |
| Full seeded real-engine CI suite | Not passed; isolated FE OOM and incomplete long-running integration verification |
| Corrected typed aggregate selection | 118 PASS, 7 FAIL, 1 BLOCKED; cleanup verified. Six failures are metric min/max-by engine plans; one is DECIMAL256 array_agg_distinct_if |
| HLL literal-configuration rerun | 190 PASS, 4 FAIL; 94 catalog and 94 concrete definitions passed; remaining DECIMALV2 failures preserved; cleanup verified |
| Full SQL attempt including geospatial | 5,019 PASS, 368 FAIL, 9,768 BLOCKED; BE SIGSEGV; separate recovery verified all four fixture groups |
| Final geospatial and MySQL protocol rerun | 75 PASS, zero FAIL/BLOCKED: all 15 geospatial functions in three inventories, 14 data scenarios, three protocol cases, 11 mysql:8.0 cases, inventory and cleanup; 93 geospatial effect checks passed |
| MySQL 8 geospatial client probes | WKT/coordinates and raw binary geometry passed using the actual mysql:8.0 client |
| Geospatial/query/proxy focused unit regressions | 209 passed; the subsequent harness and binary-result rerun passed all 90 selected tests |
| Aggregate-state DATE and HLL crashes | Reproduced; no compiled/verified BE fix |

Docker's allocation remains 7.817 GiB. Data fixtures use six rows; ML uses 36.
Tests run serially. Successful ML/stage acceptance set the planner timeout to
30,000 ms on each test connection; it did not change global engine settings.
The isolated engine was stopped after its database cleanup to avoid running two
test engines together. Its worker's DROP command and absence verification
both succeeded. The absence check uses the existing `UserService._parse_identity`
parser, and the private journal records verified cleanup.

The source matcher and public MySQL probes establish the documented shadowed
scalar and hidden operator classifications. Other unmapped definitions,
UNKNOWN_TYPE recipes, external prerequisites, and statement alternatives still
require evidence. Passing unit tests or this acceptance subset does not remove
those blockers or authorize a completion claim.
