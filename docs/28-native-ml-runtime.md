# Module 28: Native ML Runtime

> Budgeted, user-scoped ML execution over StarRocks data, with ephemeral analysis and persistent model lifecycle management.

---

## Overview

Nova exposes one deterministic ML runtime to worksheet SQL, HTTP clients, SQL
prediction, and the assistant. The language model may assemble a structured ML
request, but numerical work always runs in the ML worker process.

```text
CREATE ML_MODEL / POST /ml/execute / ml_execute
    -> MLExecutionSpec + caller security context
    -> SQL guard and @stage translation
    -> encrypted MLWorkerJob descriptor
    -> worker process
       -> StarRocks Arrow Flight SQL (MySQL batches on transport fallback)
       -> bounded Arrow RecordBatches
       -> one bounded Arrow Table for full-dataset engines
       -> FLAML                 classification / regression
       -> StatsForecast         forecasting
       -> PyOD                  anomaly detection
       -> scikit-learn          clustering
    -> ephemeral result, or checksummed artifact + registry version
```

The API process is the control plane: it sends only the execution descriptor,
prepared SQL, encrypted caller credential, and budget. The worker opens the
caller-scoped StarRocks connection and owns extraction and materialization.
One producer thread owns the full ADBC lifecycle and feeds a bounded asyncio
queue, so connection, cursor, reader, and cleanup never migrate across threads.

Arrow remains columnar until an engine boundary. FLAML, scikit-learn, PyOD, and
StatsForecast still require pandas/NumPy matrices; those unavoidable copies
happen once inside the worker and are measured. Nova does not claim literal
zero-copy execution.

## Operations

### Persistent worksheet models

Classification and regression use FLAML when `ALGORITHM = auto`. `MODE`
controls the search budget and breadth: `INTERACTIVE`, `BALANCED`, or `BEST`.

```sql
CREATE ML_MODEL churn_model
TYPE = CLASSIFICATION
TARGET = churned
FEATURES = (age, plan, active, joined_at)
MODE = BALANCED
AS SELECT age, plan, active, joined_at, churned
FROM analytics.customer_features;
```

Forecasting has a chronological validation split and is not implemented as
random tabular regression. `SERIES` is optional.

```sql
CREATE ML_MODEL revenue_forecast
TYPE = FORECAST
TARGET = revenue
TIMESTAMP = sale_date
SERIES = store_id
HORIZON = 30
FREQUENCY = 'D'
MODE = BEST
AS SELECT sale_date, store_id, revenue
FROM analytics.daily_revenue;
```

Anomaly detection and clustering are unsupervised and do not require `TARGET`.

```sql
CREATE ML_MODEL unusual_customers
TYPE = ANOMALY_DETECTION
FEATURES = (orders_30d, spend_30d, refund_rate)
AS SELECT customer_id, orders_30d, spend_30d, refund_rate
FROM analytics.customer_features;

CREATE ML_MODEL customer_segments
TYPE = CLUSTERING
FEATURES = (orders_30d, spend_30d, days_active)
AS SELECT customer_id, orders_30d, spend_30d, days_active
FROM analytics.customer_features;
```

### On-the-fly execution

`POST /api/v1/ml/execute` uses the same execution contract. The default is
`persist=false`; bounded result metadata stays in a scoped TTL/LRU cache while
the promotion-ready artifact is written under a tenant-scoped temporary object
key. No model registry row is created. Expiry or LRU eviction deletes the
temporary artifact idempotently.

```json
{
  "task": "forecast",
  "input_sql": "SELECT sale_date, store_id, revenue FROM analytics.daily_revenue",
  "target_column": "revenue",
  "timestamp_column": "sale_date",
  "series_column": "store_id",
  "horizon": 30,
  "frequency": "D",
  "mode": "interactive",
  "persist": false
}
```

Promote an unexpired run without retraining:

```http
POST /api/v1/ml/runs/{run_id}/promote
Content-Type: application/json

{"model_name":"revenue_forecast","database_name":"analytics"}
```

### Prediction

Aliases are scoped by owner and database and point to one exact
`model_id/version`. The Query API, SQL Workspace, and Nova MySQL proxy intercept
`ML_PREDICT`, extract feature rows in bounded batches, load the selected
artifact through the runtime cache, and perform one vectorized prediction call.

```sql
SELECT customer_id, ML_PREDICT('production_churn', age, plan, active)
FROM analytics.active_customers;
```

The Java scalar HTTP UDF remains only for compatibility with direct StarRocks
connections. It is not the default SQL Workspace or Query API path.

SQL prediction expressions are parsed with a balanced SQL-aware scanner rather
than a parenthesis-breaking regex. Nova evaluates nested feature expressions as
one vectorized batch and reconstructs the requested projection, so columns such
as `customer_id` are not replaced by internal feature columns. SQL result
materialization is capped by `ML_SQL_RESULT_MAX_ROWS`.

### Persisted forecast inference

Forecast bundles are not passed to generic row prediction. Use the task-aware
surfaces after an alias or immutable version has been registered:

```http
POST /api/v1/ml/forecast
{"model_alias":"revenue_forecast","horizon":30,"confidence_level":95,"series":"west"}

POST /api/v1/ml/forecast/version
{"model_id":"...","version":2,"horizon":30,"confidence_level":95}
```

The runtime loads and checksum-verifies the artifact after restart, then calls
the persisted StatsForecast model with temporal horizon semantics.

The same lifecycle is available as a Nova table-style SQL statement. Use
`MODEL` for an alias, or `MODEL_ID` together with `VERSION` for an immutable
version. `SERIES` is optional.

```sql
SELECT *
FROM ML_FORECAST(
  MODEL => 'revenue_forecast',
  HORIZON => 30,
  SERIES => 'west',
  CONFIDENCE => 95
);

SELECT *
FROM ML_FORECAST(MODEL_ID => '...', VERSION => 2, HORIZON => 30);
```

Both forms return `timestamp`, `series`, `prediction`, `lower`, and `upper`.

## Nova UI

```text
Machine Learning
  Models
    churn_model            v3  production -> v2
    revenue_forecast       v1
  Runs
    01... forecast         succeeded  ephemeral  expires in 24m
    02... classification   succeeded  persisted  v3
```

UI copy uses model, run, stage, and configured storage-connection names. It
does not expose provider-specific storage URLs or credentials.

## Implementation Notes

- Mixed numeric, categorical, boolean, and datetime features share one
  serialized preprocessing pipeline. Unknown inference categories are ignored
  safely. Unsupported Arrow types fail explicitly instead of being cast with
  `float()`.
- CPU-bound fitting runs behind `MLJobRunner` in bounded worker processes that
  are terminated when a deadline or request cancellation is reached.
- Prediction and artifact deserialization run on a dedicated bounded executor,
  never directly on FastAPI's event-loop thread.
- New model versions are serialized with joblib, SHA-256 checksummed, and
  written through a temporary object followed by verified promotion. The
  serialization envelope carries an explicit format version; legacy artifacts
  remain readable.
- A Redis ownership lock serializes version allocation across API replicas.
  Reservation creates `TRAINING`; artifact completion writes `READY`; only a
  READY version can advance `current_version`. Aborted reservations become
  `FAILED`, leaving the previous production version untouched.
- The model cache is single-flight, TTL, LRU, and byte bounded. Its key is
  `(model_id, version)`, so alias movement cannot return the wrong version.
- Run telemetry includes extraction rows, bytes, batches, queue wait, largest
  batch, and duration; worker startup, training, serialization, upload, and
  total duration; IPC mode/bytes; selected engine and algorithm; validation
  metric; artifact/result sizes; cache state; scope; lifecycle state; and a
  sanitized failure type/message.

Central configuration variables are defined in `app/core/config.py`:

```text
ML_ARROW_ENABLED                 ML_ARROW_BATCH_SIZE
ML_ARROW_QUEUE_DEPTH             ML_MYSQL_BATCH_SIZE
ML_INTERACTIVE_TIMEOUT_SECONDS
ML_BALANCED_TIMEOUT_SECONDS      ML_BEST_TIMEOUT_SECONDS
ML_MAX_INTERACTIVE_ROWS          ML_MAX_INTERACTIVE_BYTES
ML_MAX_BALANCED_ROWS             ML_MAX_BALANCED_BYTES
ML_MAX_BEST_ROWS                 ML_MAX_BEST_BYTES
ML_MAX_CONCURRENCY               ML_WORKER_PROCESSES
ML_ARTIFACT_STORAGE_CONNECTION   ML_ARTIFACT_PREFIX
ML_MODEL_CACHE_MAX_MODELS        ML_MODEL_CACHE_MAX_BYTES
ML_MODEL_CACHE_TTL_SECONDS       ML_EPHEMERAL_TTL_SECONDS
ML_EPHEMERAL_MAX_ENTRIES         ML_EPHEMERAL_MAX_MEMORY_BYTES
ML_SQL_RESULT_MAX_ROWS
ML_RANDOM_SEED
```

The repeatable local smoke benchmark is:

```bash
cd backend
uv run python scripts/benchmark_ml_data_path.py --rows 100000 --batch-size 65536

# With the engine stack running, exercise the real process/Flight boundary:
uv run python scripts/benchmark_ml_data_path.py \
  --worker-direct-sql "SELECT x, target FROM benchmark_features" \
  --database NOVA_EXAMPLE --target target
```

Reference run on the local development stack (100,000 rows, 2026-09-22):

| Path | Rows | Input bytes | Batches | Wall time | IPC dataset bytes |
|---|---:|---:|---:|---:|---:|
| Synthetic Arrow construction | 100,000 | 1,300,000 | 2 | 0.011 s extraction | n/a |
| Worker-direct Flight + ridge training | 100,000 | 1,600,000 | 25 | 11.367 s | 0 |

The worker-direct number includes process spawn, Flight extraction,
materialization, preprocessing, and model training. It is evidence of the real
production boundary, not a latency guarantee.

The same run recorded 0 bytes of API peak-RSS growth, 376,619,008 bytes API
peak RSS (the process had already reached that high-water mark), and 354,172,928
bytes worker peak RSS. Worker startup was 1.963 seconds and measured training
was 0.023 seconds. RSS values are process high-water marks, not retained-memory
measurements.

## Limitations

- Engines that need a full training matrix still concatenate bounded Arrow
  batches once inside the worker. Requests beyond configured row/byte limits
  are rejected; out-of-core Parquet materialization is not yet an execution
  mode.
- Nova SQL surfaces use vectorized `ML_PREDICT` and task-aware `ML_FORECAST`;
  direct clients that bypass Nova and connect to StarRocks cannot use those
  interception paths.
- Ephemeral cache state is process-local. Deployments with multiple backend
  replicas need a shared run-cache implementation if promotion must cross
  replicas.
