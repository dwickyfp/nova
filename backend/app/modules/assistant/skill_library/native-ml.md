---
name: native-ml
title: Use Nova's native ML runtime correctly
summary: Choose, run, explain, and persist Nova ML tasks for forecasting, classification, regression, anomaly detection, and clustering.
triggers: ml, machine, learning, forecast, forecasting, ramal, prediksi, classification, klasifikasi, regression, regresi, anomaly, anomali, outlier, clustering, cluster, segmentasi, ml_predict, ml_forecast
source: docs/28-native-ml-runtime.md
---

# Native ML

Use Nova's ML runtime for numerical ML work. Do not imitate a model result with
prose, hand-written scores, or arbitrary SQL. Keep the user's StarRocks identity,
database, schema, and role as the security boundary.

## Choose the surface

- Use `ml_execute` for an assistant-run analysis. Prefer `persist=false` for an
  exploratory result. It is read-only and ephemeral.
- Use `persist=true` only when the user asks to save the model. It is a write,
  requires consent, and also requires `model_name`.
- Write `CREATE ML_MODEL` when the user asks for worksheet SQL or a reusable DDL
  statement. Explain that the user must run it; never claim it trained already.
- Use `ML_PREDICT` for row-wise classification or regression inference through
  Nova SQL. Use `ML_FORECAST` for future time-series rows. Forecast bundles do
  not use generic `ML_PREDICT`.

## Choose the task

| Intent | `task` | Required inputs | Useful optional inputs |
|---|---|---|---|
| Predict a class, churn, fraud, or category | `classification` | `input_sql`, `target` | `feature_columns` |
| Predict a numeric value | `regression` | `input_sql`, `target` | `feature_columns` |
| Predict future values over time | `forecast` | `input_sql`, `target`, `timestamp`, `horizon` | `series`, `frequency` |
| Find unusual rows | `anomaly_detection` | `input_sql` | `feature_columns`, `row_identifier`, `parameters.contamination` |
| Segment similar rows | `clustering` | `input_sql` | `feature_columns`, `row_identifier`, `parameters.max_clusters` |

Ask one focused question when a required column or forecast horizon is missing.
Do not invent a target, timestamp, or feature meaning.

## Run with `ml_execute`

Pass exact column names returned by `input_sql`:

```json
{
  "task": "forecast",
  "input_sql": "SELECT sale_date, store_id, revenue FROM analytics.daily_revenue",
  "target": "revenue",
  "timestamp": "sale_date",
  "series": "store_id",
  "horizon": 30,
  "frequency": "D",
  "mode": "interactive",
  "persist": false
}
```

Modes are bounded execution profiles:

- `interactive`: quickest search; use for exploration.
- `balanced`: broader search; use when quality matters more than latency.
- `best`: widest supported search; use only when the user accepts more runtime.

The runtime rejects work beyond configured time, row, or byte limits. Never
promise that a large query will complete. Restrict the source query to the
needed columns and rows. `@stage` remains the only user-facing storage syntax.

Task-specific rules:

- Classification and regression need at least 10 rows with a non-null target.
- Forecast validation is chronological. Each series needs at least
  `max(10, horizon + 2)` observations. Missing or duplicate timestamps are
  errors. Set `parameters.insufficient_series_policy` to `drop` only when the
  user accepts excluding short series; otherwise keep the default `reject`.
- Anomaly detection needs at least 10 rows. `contamination` must be greater than
  0 and less than 0.5. A higher `anomaly_score` means more anomalous, on a
  normalized 0-to-1 rank scale.
- Clustering needs at least 6 rows. Use `row_identifier` when results must map
  back to a business key.
- Estimator parameters for classification or regression require an explicit
  algorithm. Do not combine estimator parameters with `algorithm=auto`.

## Author persistent model SQL

Supported model types are `CLASSIFICATION`, `REGRESSION`, `FORECAST`,
`ANOMALY_DETECTION`, and `CLUSTERING`. `MODE` is `INTERACTIVE`, `BALANCED`, or
`BEST`.

```sql
CREATE ML_MODEL revenue_forecast
TYPE = FORECAST
TARGET = revenue
TIMESTAMP = sale_date
SERIES = store_id
HORIZON = 30
FREQUENCY = 'D'
MODE = BALANCED
AS SELECT sale_date, store_id, revenue
FROM analytics.daily_revenue;
```

Classification, regression, and forecast require `TARGET`. Forecast also
requires `TIMESTAMP` and a positive `HORIZON`. Anomaly detection and clustering
are unsupervised and must not be given a synthetic target.

When `HYPERPARAMETERS` is present it must be a JSON object. With tabular models,
choose an explicit `ALGORITHM`; auto-search must not silently ignore estimator
parameters.

## Run persisted inference

Classification or regression:

```sql
SELECT customer_id,
       ML_PREDICT('production_churn', age, plan, active) AS prediction
FROM analytics.active_customers;
```

Forecast by alias:

```sql
SELECT *
FROM ML_FORECAST(
  MODEL => 'revenue_forecast',
  HORIZON => 30,
  SERIES => 'west',
  CONFIDENCE => 95
);
```

Use `MODEL_ID` together with `VERSION` when an immutable version is required.
Forecast output contains `timestamp`, `series`, `prediction`, `lower`, and
`upper`. Aliases resolve to one exact ready model version.

## Report results honestly

- State the task, selected algorithm, processed row count, and run id from tool
  evidence. Include validation metrics when the result provides them.
- For clustering, explain cluster ids as labels, not inherent rankings.
- For anomaly detection, an anomaly is a review signal, not proof of fraud or
  wrongdoing.
- Do not claim success without a verified run artifact. A drafted SQL statement
  is not a trained model.
- Never expose credentials, provider-specific object-storage paths, internal
  artifact URIs, or raw secrets in SQL, output, errors, or logs.
