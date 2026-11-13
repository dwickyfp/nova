---
title: Machine learning with SQL
description: Train classification, regression, clustering, anomaly, and forecast models from a worksheet.
---

## Before you start

Use a SQL worksheet and a database where your active role can create tables.
The training query runs with your data permissions. These invented rows are
for learning the commands, not for evaluating a real business model.

Create a separate example database and select it in the worksheet:

```sql
CREATE DATABASE ml_examples;
USE ml_examples;

CREATE TABLE training_examples AS
SELECT generate_series AS sample_id,
       MOD(generate_series, 10) AS visits,
       CAST(20 + MOD(generate_series, 10) * 3 AS DOUBLE) AS spend,
       IF(MOD(generate_series, 10) >= 5, 1, 0) AS loyal,
       DATE_ADD('2026-01-01', INTERVAL generate_series DAY) AS day
FROM TABLE(generate_series(1, 120));
```

This creates 120 rows. Keep the database selected for training, alias creation,
and prediction. A model name has no database prefix; use the worksheet's
selected database to place it in the right scope.

## Classification and regression

Classification predicts a category. This example predicts `loyal` from
`visits` and `spend`:

```sql
CREATE ML_MODEL loyalty
TYPE = CLASSIFICATION TARGET = loyal ALGORITHM = random_forest
AS SELECT visits, spend, loyal FROM training_examples;
```

Regression predicts a number. Numeric targets can include decimal columns:

```sql
CREATE ML_MODEL spending
TYPE = REGRESSION TARGET = spend ALGORITHM = random_forest
AS SELECT visits, spend FROM training_examples;
```

Wait for each statement to finish. Its result includes the model ID, version,
training row count, features, and evaluation metrics. The example regression
learns an intentionally simple relationship; its high score is not evidence
that a real model will perform equally well.

## Clustering and anomaly detection

Clustering groups similar rows without a target column:

```sql
CREATE ML_MODEL segments
TYPE = CLUSTERING
AS SELECT visits, spend FROM training_examples;
```

Anomaly detection identifies unusual combinations of inputs:

```sql
CREATE ML_MODEL unusual_spend
TYPE = ANOMALY_DETECTION
AS SELECT visits, spend FROM training_examples;
```

These statements choose an algorithm from the available candidates. Keep
identifiers such as `sample_id` out of the training features.

## Predict through an alias

In **AI & ML → ML Models → Aliases**, create aliases for the ready versions in
`ml_examples`: `loyalty_current`, `spending_current`, `segments_current`, and
`unusual_spend_current`, each pointing to its corresponding model.

Return to the worksheet with the same database selected:

```sql
SELECT sample_id, ML_PREDICT('loyalty_current', visits, spend) AS prediction
FROM training_examples LIMIT 5;

SELECT sample_id, ML_PREDICT('spending_current', visits) AS prediction
FROM training_examples LIMIT 5;

SELECT sample_id, ML_PREDICT('segments_current', visits, spend) AS prediction
FROM training_examples LIMIT 5;

SELECT sample_id, ML_PREDICT('unusual_spend_current', visits, spend) AS prediction
FROM training_examples LIMIT 5;
```

Each statement returns five rows. Pass inputs in the order shown in the
training result's `feature_columns`. Do not include the target or an identifier
unless it was deliberately a training feature.

## Forecast future values

A forecast needs a timestamp, target, and horizon. Here the frequency is daily
(`D`) and the horizon is three days:

```sql
CREATE ML_MODEL daily_spend
TYPE = FORECAST TARGET = spend TIMESTAMP = day
HORIZON = 3 FREQUENCY = 'D' ALGORITHM = naive
AS SELECT day, spend FROM training_examples;
```

Create `daily_spend_current` in **Aliases**, pointing to the new ready version
in the same database. Then run:

```sql
SELECT * FROM ML_FORECAST(MODEL => 'daily_spend_current', HORIZON => 3);
```

The result contains three forecast rows. A naive forecast uses the latest
observed value as its baseline; it does not infer a business trend.

## Clean up

Delete the example aliases and models from **ML Models** when finished, then
remove the example database only if it contains no work you need:

```sql
DROP DATABASE ml_examples;
```

## Limits and errors

- Training needs enough usable rows. Classification also needs enough examples
  of its categories for the validation split.
- Use numeric targets for regression and a consistent date frequency for
  forecasting.
- Every column you name in `TARGET`, `TIMESTAMP`, `SERIES`, or `FEATURES` must
  be in the training query's `SELECT` list, spelled with the same letter case.
  Otherwise Nova refuses the statement before training starts and no model is
  saved. The message names the column and lists the columns the query
  returns, for example `TARGET column 'loyal' is not returned by the training
  query. Its columns are: visits, spend. Add 'loyal' to the SELECT list, or
  name one of these columns.`
- A model's database and its alias database must match.
- Model training can take longer than a normal query. Inspect its status and
  metrics before creating an alias.
- Use a Nova worksheet to train models. A MySQL client session cannot run
  `CREATE ML_MODEL`; see [MySQL clients](/docs/sql/mysql-clients).
