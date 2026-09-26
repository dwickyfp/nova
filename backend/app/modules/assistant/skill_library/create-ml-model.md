---
name: create-ml-model
title: Train an ML model with CREATE ML_MODEL
summary: Author a valid persistent CREATE ML_MODEL worksheet statement for Nova's native ML runtime.
triggers: create, ml_model, ddl, worksheet, train, latih
source: docs/28-native-ml-runtime.md
---

# Skill: create-ml-model

Author a `CREATE ML_MODEL` worksheet statement. Nova intercepts the statement
and runs its bounded native ML runtime; the DDL is not sent to StarRocks as-is.
Do not claim that a drafted statement has trained a model.

## Grammar

```
CREATE ML_MODEL <model_name>
  TYPE = CLASSIFICATION | REGRESSION | FORECAST
       | ANOMALY_DETECTION | CLUSTERING
  [TARGET = <target_column>]
  [TIMESTAMP = <timestamp_column>]
  [SERIES = <series_column>]
  [HORIZON = <positive_integer>]
  [FREQUENCY = '<frequency>']
  [MODE = INTERACTIVE | BALANCED | BEST]
  [ALGORITHM = <supported_algorithm>]
  [TEST_SIZE = <0 ≤ x < 1>]
  [FEATURES = (<col>, <col>, …)]
  [HYPERPARAMETERS = JSON '{"n_estimators": 100}']
  AS SELECT …
```

- `TYPE` is always required.
- Classification, regression, and forecast require `TARGET`.
- Forecast also requires `TIMESTAMP` and `HORIZON`; `SERIES` and `FREQUENCY`
  are optional.
- Anomaly detection and clustering are unsupervised and do not require
  `TARGET`.
- The training query must start with `SELECT`.
- `TEST_SIZE` default `0.2`; must satisfy `0 ≤ x < 1`.
- `HYPERPARAMETERS` must be a JSON **object**.
- `FEATURES` must list at least one column.
- Estimator hyperparameters for classification or regression require an
  explicit algorithm; they cannot be combined with `ALGORITHM = auto`.

## Minimal template

```sql
CREATE ML_MODEL <name>
  TYPE = CLASSIFICATION
  TARGET = <target_column>
  AS SELECT <feature_cols...>, <target_column> FROM <db>.<table>
```

## Forecast template

```sql
CREATE ML_MODEL `revenue_forecast`
  TYPE = FORECAST
  TARGET = `revenue`
  TIMESTAMP = `sale_date`
  SERIES = `store_id`
  HORIZON = 30
  FREQUENCY = 'D'
  MODE = BALANCED
  AS SELECT sale_date, store_id, revenue FROM analytics.daily_revenue
```

## Training from a stage file

`@stage` is allowed in the training query; it is translated and credentials are
injected before the engine sees it. The stored `training_sql` is redacted.

```sql
CREATE ML_MODEL sentiment_model
  TYPE = CLASSIFICATION
  TARGET = label
  AS SELECT review_text, label FROM @reviews.training.csv
```

## Result and lifecycle

- Response columns: `model_id, model_name, model_type, algorithm, version,
  status, training_rows, feature_columns, metrics`.
- Persistence reserves a `TRAINING` version, promotes a checksummed artifact,
  and then marks the version `READY`. A failed run must not replace the previous
  ready version.
- Use `ML_PREDICT` for classification or regression inference. Use the
  table-style `ML_FORECAST` statement for persisted forecast inference.

## Caveats

- Classification and regression need at least 10 valid target rows. Forecast
  needs at least `max(10, horizon + 2)` observations per series. Anomaly
  detection needs 10 rows; clustering needs 6.
- Draft and explain SQL when requested. For explicit execution use query_mutate
  with approval, or ml_execute for an analysis workflow. Claim a trained model
  only after a successful runtime result.
