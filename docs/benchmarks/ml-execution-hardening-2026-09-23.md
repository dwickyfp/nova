# ML execution hardening, 2026-09-23

> Implementation and validation report for worker artifacts, SQL prediction, durable runs, and bounded inference.

## A. Executive summary

Training now publishes its artifact inside the worker. The API receives a
descriptor rather than an estimator or artifact payload. Prediction SQL uses
StarRocks AST nodes, ephemeral descriptors survive API restarts, and large
row-prediction outputs have a worker-to-Parquet path. Changes are local; this
work did not deploy the application or migrate a production database.

## B. Worker artifact architecture

```text
API: reserve version and persist upload lease
  -> worker: extract -> preprocess -> train -> serialize -> checksum
  -> storage: staging object -> verify size/SHA-256 -> publish final object
  -> API: descriptor -> READY registry version or durable ephemeral descriptor
```

The same artifact boundary serves persistent and ephemeral training. Worker
results contain no model bundle or Arrow result table. Previews are capped at
1,000 rows; serialized result metadata is capped at 1 MiB. The recorded IPC
size measures the pickled result object, excluding queue framing and the small
job descriptor sent to the worker.

Upload leases let another replica discover artifacts left by terminated
workers. Cleanup removes staging objects and checks READY versions before
deleting final artifacts. Registration and abort share a version lock;
registration rejects reservations that are no longer TRAINING. No production
worker-direct runner may silently switch to the embedded dataset/estimator IPC
path. Inline runners remain an explicit test/embedding seam.

## C. SQL ML architecture

`ML_PREDICT` discovery and projection planning use the generated StarRocks AST.
Comments, CTE inputs, nested feature expressions, and multiple standalone
prediction expressions retain their requested projection positions. Hidden
feature columns are removed from the returned table.

The planner rejects unsupported wrappers, wildcard projections, aggregation,
set operations, ordering, and predictions in filters/subqueries. It does not
silently remove ROUND, CAST, CASE, or arithmetic. `ML_PREDICT_TABLE` requires a
complete standalone SELECT with literal alias and input-query arguments.

The separate, pre-existing named-argument `ML_FORECAST` recognizer remains a
compatibility parser. It accepts only the complete table form; it does not
perform scalar projection rewriting. Its `=>` extension is not accepted by
the vendored StarRocks table-function grammar.

## D. Large inference

`POST /api/v1/ml/predict/materialize` and standalone `ML_PREDICT_TABLE` submit a
worker job containing encrypted SQL and caller credentials. The worker reads
input, predicts, and writes Parquet parts plus a manifest. The API returns a
result handle and retrieves one bounded part per page.

Defaults: 4,096 prediction rows per batch, 16 MiB logical Arrow batch bytes,
100,000 rows/64 MiB for inline results. These limits are configurable. The
large-result path does not convert the complete output to Python rows.

An incoming Flight batch and ML-library scratch memory are separate from the
logical prediction-batch bound. Training still materializes a bounded matrix
inside the worker. Model-cache memory uses a configurable four-times-artifact
estimate, not an exact process RSS limit.

## E. Ephemeral architecture

`NOVA_SYSTEM.ML_EPHEMERAL_RUNS` stores scoped descriptors, sanitized execution
specifications, expiration timestamps, and pending upload leases. Local LRU
eviction never deletes a shared artifact. Another API replica can resolve and
promote a live run without retraining or downloading the artifact into the API.

Promotion preserves task, algorithm, target, timestamp, series, frequency,
horizon, feature metadata, and non-secret parameters. A copy checksum protects
promotion. A startup sweeper performs storage deletion off the event loop and
retries failures. Result cleanup enumerates the run's object prefix, so retry
does not depend on a manifest that may already have been deleted. Fingerprint
eviction checks the mapped run ID before deleting the fingerprint mapping.

## F. Deadline model

Each execution has one monotonic deadline. Queue admission, process startup,
extraction, preprocessing/training, serialization, upload, and registration
consume that budget. Training receives the remaining time with a two-second
finalization reserve. The child reports its active phase through shared state;
timeout errors identify the phase. Cancellation terminates the worker and
leaves a durable cleanup destination if completion was not acknowledged.

## G. Memory safety

Categorical preprocessing caps each encoded column at 100 categories and
retains sparse output. Numeric allocations and sparse-to-dense conversions
check the 256 MiB dense-matrix budget before allocation. Dense-only histogram
estimators use the same guard during fitting and inference. Single-flight model
and registry lock records are released after the last waiter.

Forecast responses remain inline: horizon times series count is bounded before
prediction, returned Arrow bytes are capped, and training previews contain at
most 1,000 rows. Larger row-prediction workloads use result materialization.

## H. Security

Model queries and alias namespaces include tenant and owner. Artifact scopes
and ephemeral/result lookup scopes include tenant, principal, role, security
revision, database, and schema. Canonical tuple hashing prevents delimiter
collisions. HTTP, query, assistant ML, and worker boundaries carry their trusted
tenant context. The relayed proxy retains its existing single-tenant identity.

Prepared SQL and passwords are encrypted before worker dispatch. Persisted
specifications exclude security credentials and redact storage credentials.
Unknown worker failures cross IPC as phase/type summaries rather than raw
provider exceptions. Public materialized responses omit storage URIs.

## I. Observability

Measurements include actual transport (including MySQL fallback), extraction
rows/bytes/batches, largest batch, startup/training/serialization/upload time,
remaining training budget, configured budget, artifact size/checksum, worker
peak RSS, result rows/bytes/parts, timeout stage, and separate dataset,
trained-model, and worker-result IPC byte fields. Zero dataset/model IPC values
apply to the descriptor-only worker path; embedded test execution reports its
dataset size instead.

## J. Database migrations

`backend/migrations/20260922_ml_ephemeral_runs.sql` adds `ML_MODELS.tenant_name`
with default `default` and creates the Primary Key ephemeral-descriptor table.
Startup checks column existence before the ALTER and preserves tenant values
when upgrading a legacy model table. Fresh Docker initialization includes the
new schema. The raw ALTER script is a once-only upgrade, not an idempotent SQL
file; startup's migration is idempotent.

## K. Tests

Commands below ran from `backend/` using its existing virtual environment.

```sh
.venv/bin/python -m pytest tests/unit tests/eval tests/integration/test_native_ml_pipeline.py -q
```

Result: 3,391 passed, three warnings. The warnings concern the existing
Starlette/httpx deprecation and deliberately degenerate clustering fixtures.
The first sandboxed run could not bind test sockets; the final run had local
test-socket access. No unrelated failures remained in that run.

```sh
COMPOSE_PROJECT_NAME=nova-ml-hardening-check \
NOVA_TEST_FE_MYSQL_PORT=39030 NOVA_TEST_FE_HTTP_PORT=38030 \
NOVA_TEST_FE_ARROW_PORT=39408 NOVA_TEST_MINIO_PORT=39000 \
NOVA_TEST_REDIS_PORT=36379 \
.venv/bin/python -m pytest tests/integration/test_native_ml_arrow_l3.py -q -rs
```

Result: three passed, one ADBC autocommit warning, 53.67 seconds. This isolated
stack exercises Arrow Flight, concurrent registry versions, additive tenant
DDL, and an actual spawned worker uploading to storage. Default ports initially
collided with an existing stack; the isolated project used alternate ports and
the test fixture removed only its own generated stack afterward.

```sh
.venv/bin/python -m tests.eval.report
.venv/bin/mypy app/modules/ml_engine app/common/ml_intercept.py --ignore-missing-imports --follow-imports=silent
```

Scorecard: 32/32 scenarios, 96/96 checks. Targeted type checking: no issues in
46 source files. Changed Python modules were formatted and linted with Ruff;
`git diff --check` passed. No runtime dependencies or container build inputs
changed, so no new application image build was required.

Final formatting/lint commands:

```sh
.venv/bin/ruff format --check app/common/ml_intercept.py app/common/nova_system.py app/modules/ml_engine scripts/benchmark_ml_data_path.py tests/unit/test_ml_execution_hardening.py tests/unit/test_ml_artifact_upload.py tests/integration/test_native_ml_arrow_l3.py
.venv/bin/ruff check app/common/ml_intercept.py app/common/nova_system.py app/modules/ml_engine scripts/benchmark_ml_data_path.py tests/unit/test_ml_execution_hardening.py tests/unit/test_ml_artifact_upload.py tests/integration/test_native_ml_arrow_l3.py
git diff --check
```

Result: 51 Python files already formatted; lint and whitespace checks passed.

After the final forecast/projection and telemetry adjustments, these focused
checks also passed:

```sh
.venv/bin/python -m pytest tests/unit/test_native_ml_architecture.py tests/unit/test_create_ml_model_forecast.py tests/unit/test_ml_execution_hardening.py tests/integration/test_native_ml_pipeline.py -q
.venv/bin/python -m pytest tests/unit/test_ml_boundary_hardening.py tests/unit/test_ml_artifact_upload.py -q
```

Results: 78 passed (three known clustering/core-detection warnings) and
22 passed, respectively. These overlap the broad suite and are not additional
unique-test counts. Formatting, lint and targeted mypy were rerun afterward and
passed.

Tests added cover real spawn/descriptor isolation, one-million-row bounded
inference, failed/checksum-corrupt uploads, metric direction and selection,
sparse allocation guards, high-cardinality encoding, lock cleanup, SQL syntax
and rejection cases, durable replica promotion, security-scope isolation,
abandoned upload cleanup, result cleanup retry, and migration idempotency.

## L. Benchmarks

```sh
.venv/bin/python -m scripts.benchmark_ml_data_path --rows 100000 --inference-rows 1000000 --batch-size 10000
```

The spawned-worker benchmark uses synthetic Arrow input and temporary local
disk storage. It exercises training, artifact serialization/loading, inference,
and Parquet output; these are not production storage throughput numbers.

| Measurement | Observed value |
| --- | ---: |
| Training rows | 100,000 |
| Training time | 0.019843 s |
| Serialization time | 0.000710 s |
| Local artifact write time | 0.000292 s |
| Artifact bytes | 1,405 |
| Serialized worker-result bytes | 1,763 |
| Dataset / trained-model IPC bytes | 0 / 0 |
| Inference rows | 1,000,000 |
| Parquet parts | 300 |
| Parquet output bytes | 20,721,188 |
| Largest logical input prediction batch | 65,536 bytes |
| Inference and result-write time | 1.385251 s |
| Worker peak RSS | 363,741,184 bytes |
| Whole spawned pipeline | 5.550005 s |
| API peak-RSS growth | 0 bytes |

RSS is a process high-water mark. The API had already run the legacy/synthetic
comparison, so zero additional peak growth is not a claim of zero allocation.
The run emitted writable-cache warnings from Matplotlib/Fontconfig; it exited
successfully. Timing varies with process startup and concurrent machine load.

## M. Supported limits and operational follow-up

Training engines remain in-memory within their configured input limits.
Wrapped scalar ML expressions are explicitly unsupported; use separate
application processing or a materialized result for follow-up work. Forecasts
use capped inline responses rather than the row-prediction materialization API.

Relayed MySQL clients do not provide a reusable password to start new worker
connections. They retain bounded inline prediction; large materialization
requires an authenticated API session. Operators should size worker concurrency,
memory budgets, artifact lifecycle permissions and cache estimates for their
deployment. Production rollout and application of the migration to production
remain deployment actions, not actions performed by this implementation task.

## Antislop delivery gate

- R-02 PASS: newly written report prose uses no em dash; revised comments explain contracts or failure handling.
- R-17 PASS: every benchmark number above comes from the executed benchmark output.
- R-18 PASS: no testimonials or invented people were added.
- R-23 PASS: no visual assets or navigation structure were created.
- R-33 PASS: source changes used patches; Ruff performed formatting only.
- R-35 PASS: backend code was exercised by unit, trajectory and real-engine tests; no interactive UI was added.
- R-36 PASS: performance claims state the synthetic source, local storage and RSS measurement limits.
- R-37 PASS: direction came from the supplied engineering guide; visual direction and ENERGY/RHYTHM/MOTION are not applicable to this backend task.
- R-38 PASS: no fabricated customer, security-certification or production-throughput claims appear.
- C-1 PASS: documentation describes the implemented execution paths and their bounds.
- C-2 PASS: new API paths have implementations; there are no added UI controls.
- C-3 PASS: sections A through M follow the user's requested report structure.
- C-4 PASS: cancellation, cleanup retry and rejected SQL paths have tests; no layout or theme changed.
- C-5 PASS: test counts, commands and benchmark conditions are recorded above.
- UI-only gates R-01, R-03 through R-16, R-19 through R-22, R-24 through R-32 and R-34: not applicable; this task adds no screens, styles, fonts, animation or controls.
- Liveliness block: not applicable to source and engineering documentation; no visual design was generated.
