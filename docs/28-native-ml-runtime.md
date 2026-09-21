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
    -> StarRocks Arrow Flight SQL (MySQL batches on transport fallback)
    -> bounded Arrow Table
    -> worker process
       -> FLAML                 classification / regression
       -> StatsForecast         forecasting
       -> PyOD                  anomaly detection
       -> scikit-learn          clustering
    -> ephemeral result, or checksummed artifact + registry version
```

The primary transport is Arrow Flight SQL on port `9408`. Batches retain Arrow
types and are checked against row and byte limits while they arrive. Algorithms
that require a complete matrix receive one columnar table after extraction;
the primary path never builds `list[dict]` training rows or calls unlimited
`fetchall()`.

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
`persist=false`; results and the promotion-ready artifact stay in a scoped TTL
cache and no model registry row is created.

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
- New model versions are serialized with joblib, SHA-256 checksummed, and
  written to configured Nova storage. StarRocks stores only the opaque artifact
  URI and metadata. Legacy base64 artifacts remain readable.
- Logical model identity is stable for owner/database/name; retraining advances
  `current_version`. Alias rows use a scoped StarRocks Primary Key.
- The model cache is single-flight, TTL, LRU, and byte bounded. Its key is
  `(model_id, version)`, so alias movement cannot return the wrong version.
- Run telemetry includes extraction rows, bytes, batches and duration; training
  and total duration; selected engine and algorithm; validation metric;
  artifact size; scope; status; and sanitized failure class/message.

Central configuration variables are defined in `app/core/config.py`:

```text
ML_ARROW_ENABLED                 ML_ARROW_BATCH_SIZE
ML_MYSQL_BATCH_SIZE              ML_INTERACTIVE_TIMEOUT_SECONDS
ML_BALANCED_TIMEOUT_SECONDS      ML_BEST_TIMEOUT_SECONDS
ML_MAX_INTERACTIVE_ROWS          ML_MAX_INTERACTIVE_BYTES
ML_MAX_CONCURRENCY               ML_WORKER_PROCESSES
ML_ARTIFACT_STORAGE_CONNECTION   ML_ARTIFACT_PREFIX
ML_MODEL_CACHE_MAX_MODELS        ML_MODEL_CACHE_MAX_BYTES
ML_MODEL_CACHE_TTL_SECONDS       ML_EPHEMERAL_TTL_SECONDS
ML_RANDOM_SEED
```

The repeatable local smoke benchmark is:

```bash
cd backend
uv run python scripts/benchmark_ml_data_path.py --rows 100000 --batch-size 65536
```

## Limitations

- Engines that need a full training matrix still concatenate bounded Arrow
  batches once in the worker input. Requests beyond configured row/byte limits
  are rejected; out-of-core Parquet materialization is not yet an execution
  mode.
- Nova SQL surfaces use vectorized `ML_PREDICT`; direct clients that connect to
  StarRocks instead of Nova cannot use that path.
- Ephemeral cache state is process-local. Deployments with multiple backend
  replicas need a shared run-cache implementation if promotion must cross
  replicas.
