# Module 19: Machine Learning and AI Functions

> SQL-native model training and prediction, plus managed LLM convenience functions.

---

## Overview

Nova provides two separate intelligence layers:

| Layer | Purpose | Runtime |
|---|---|---|
| Native ML | Classification, regression, forecasting, anomaly detection, clustering | Nova's deterministic ML workers |
| AI SQL functions | Completion, summarization, sentiment, translation, extraction | Configured LLM providers through StarRocks `ai_query()` |

The assistant orchestrates these systems. It does not train models or calculate
predictions itself. See [Module 28](28-native-ml-runtime.md) for the native ML
architecture, budgets, artifact lifecycle, caching, and operations.

## Operations

### Train a persistent model

`CREATE ML_MODEL` is Nova SQL and must be submitted through the SQL Workspace,
Query API, or Nova MySQL proxy rather than directly to StarRocks port `9030`.

```sql
CREATE ML_MODEL churn_model
TYPE = CLASSIFICATION
TARGET = churned
FEATURES = (age, plan, active, joined_at)
ALGORITHM = auto
MODE = BALANCED
TEST_SIZE = 0.2
AS SELECT age, plan, active, joined_at, churned
FROM analytics.customer_features;
```

Supported tasks:

| Type | Required task fields | Engine behavior |
|---|---|---|
| `CLASSIFICATION` | `TARGET` | Budgeted FLAML search and held-out evaluation |
| `REGRESSION` | `TARGET` | Budgeted FLAML search and held-out evaluation |
| `FORECAST` | `TARGET`, `TIMESTAMP`, `HORIZON` | Chronological StatsForecast evaluation; optional `SERIES` and `FREQUENCY` |
| `ANOMALY_DETECTION` | Feature query | Unsupervised PyOD scoring; no label required |
| `CLUSTERING` | Feature query | Bounded automatic candidate/K selection; no target required |

Mixed numeric, categorical, boolean, and datetime features are supported.
Training and inference use the same serialized preprocessing pipeline.

### Forecast

```sql
CREATE ML_MODEL sales_forecast
TYPE = FORECAST
TARGET = sales_amount
TIMESTAMP = sale_date
SERIES = store_id
HORIZON = 30
FREQUENCY = 'D'
MODE = BEST
AS SELECT sale_date, store_id, sales_amount
FROM analytics.daily_sales;
```

Validation always follows the training period. The result contains future
timestamps, predictions, series identifiers, and interval bounds when the
selected forecasting model provides them.

### Detect anomalies

```sql
CREATE ML_MODEL unusual_orders
TYPE = ANOMALY_DETECTION
FEATURES = (amount, item_count, account_age_days)
MODE = INTERACTIVE
AS SELECT order_id, amount, item_count, account_age_days
FROM analytics.order_features;
```

Normal anomaly detection is unsupervised and returns an anomaly score plus a
thresholded flag. A target column is not required.

### Cluster rows

```sql
CREATE ML_MODEL customer_segments
TYPE = CLUSTERING
FEATURES = (orders_30d, spend_30d, days_active)
AS SELECT customer_id, orders_30d, spend_30d, days_active
FROM analytics.customer_features;
```

### Predict

Create or move an alias with `POST /api/v1/ml/aliases`, then predict through the
ML API or intercepted SQL:

```sql
SELECT customer_id, ML_PREDICT('production_churn', age, plan, active)
FROM analytics.active_customers;
```

Nova reads feature rows in bounded batches and invokes the model vectorially.
The SQL Workspace, Query API, and Nova MySQL proxy do not use the legacy scalar
HTTP UDF.

### Run an ephemeral analysis

Use `POST /api/v1/ml/execute` with `persist=false`, or the assistant's
`ml_execute` tool. Ephemeral runs are owner/database scoped, expire after a
configured TTL, and can be promoted without retraining:

```http
POST /api/v1/ml/runs/{run_id}/promote
Content-Type: application/json

{"model_name":"sales_forecast","database_name":"analytics"}
```

### AI SQL functions

Provider and model connections are managed by administrators. Credentials are
resolved server-side and are never returned by the API or placed in SQL editor
state.

```sql
SELECT AI_SENTIMENT(review_text) FROM reviews;
SELECT AI_SUMMARIZE(article_body, 100) FROM news;
SELECT AI_TRANSLATE(text, 'en', 'id') FROM documents;
SELECT AI_CLASSIFY(ticket_text, ARRAY('billing', 'technical', 'general'))
FROM tickets;
SELECT AI_EXTRACT(order_email, ARRAY('order_id', 'customer_name', 'amount'))
FROM emails;
SELECT AI_COMPLETE('OpenAI/gpt-4o', 'Summarize: ' || text) FROM docs;
```

## Nova UI

```text
Machine Learning
  Models       stable model identity, versions, aliases, metrics
  Runs         persistent and ephemeral execution status
  Predictions  SQL/API batch inference

AI Providers
  Providers    endpoint and status (credential values hidden)
  Models       provider model names and defaults
  Functions    managed AI SQL function aliases
```

The UI displays stage names and configured storage-connection names. It never
shows provider-specific storage URLs or credential values.

## Implementation Notes

- Every feature query executes as the requesting StarRocks user and active
  role. `@stage` translation follows the same guarded SQL pipeline as worksheet
  queries.
- Arrow Flight SQL on port `9408` is the primary feature transport. The MySQL
  fallback uses bounded `fetchmany()` batches.
- CPU-heavy fitting runs outside the async request loop in bounded, terminable
  worker processes.
- New model versions live in configured Nova object storage and are verified by
  SHA-256. Registry rows contain opaque artifact URIs, not credentials or new
  base64 blobs. Legacy `model_binary` rows remain readable.
- Logical model IDs remain stable across retraining. Versions increase, and
  aliases are deterministic owner/database-scoped Primary Key rows.
- SQL-facing ML actions continue to use Nova audit logging. ML-specific run
  telemetry is stored in `NOVA_SYSTEM.ML_RUNS` for every execution attempt.

## Limitations

- Full-matrix algorithms still require one bounded Arrow table in the worker.
  Requests above configured row or byte limits must be narrowed; an out-of-core
  Parquet worker mode is not implemented yet.
- Ephemeral run state is process-local and cannot be promoted from a different
  backend replica without a shared cache.
- Direct connections that bypass Nova cannot use Nova-only DDL or the
  vectorized SQL prediction interceptor.
