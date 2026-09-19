---
name: create-ml-model
title: Train an ML model with CREATE ML_MODEL
summary: Author a valid Nova CREATE ML_MODEL statement that the Python ML engine intercepts and trains.
triggers: create ml_model, train model, classification, regression, machine learning, ml model, train, latih model
source: docs/sql_docs/03-ml-model-ddl.md
---

# Skill: create-ml-model

Author a `CREATE ML_MODEL` statement. Nova **intercepts** it in Python and trains
a scikit-learn model; it is **never** sent to StarRocks in this form. There is no
engine equivalent.

## Grammar

```
CREATE ML_MODEL <model_name>
  TYPE = CLASSIFICATION | REGRESSION
  TARGET = <target_column>
  [ALGORITHM = auto | linear | logistic | decision_tree | random_forest
             | gradient_boost | knn | svm]
  [TEST_SIZE = <0 ≤ x < 1>]
  [FEATURES = (<col>, <col>, …)]
  [HYPERPARAMETERS = JSON '{"n_estimators": 100}']
  AS SELECT …
```

- `TYPE` and `TARGET` are **required**; everything else is optional.
- The training query must start with `SELECT`.
- `TEST_SIZE` default `0.2`; must satisfy `0 ≤ x < 1`.
- `HYPERPARAMETERS` must be a JSON **object**.
- `FEATURES` must list at least one column.
- Algorithm support: classification = logistic, decision_tree, random_forest,
  gradient_boost, knn, svm; regression = linear, decision_tree, random_forest,
  gradient_boost, knn, svm. `auto` picks by row count.

## Minimal template

```sql
CREATE ML_MODEL <name>
  TYPE = CLASSIFICATION
  TARGET = <target_column>
  AS SELECT <feature_cols...>, <target_column> FROM <db>.<table>
```

## Full template

```sql
CREATE ML_MODEL `revenue_model`
  TYPE = REGRESSION
  TARGET = `revenue`
  FEATURES = (`visits`, spend)
  ALGORITHM = random_forest
  TEST_SIZE = 0.25
  HYPERPARAMETERS = JSON '{"n_estimators": 100}'
  AS SELECT visits, spend, revenue FROM fact_sales
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

## Output and storage

- Response columns: `model_id, model_name, model_type, algorithm, version,
  status, training_rows, feature_columns, metrics`.
- Metadata is stored in `NOVA_SYSTEM.ML_MODELS` and `NOVA_SYSTEM.ML_MODEL_VERSIONS`
  (`training_sql` is the redacted form).

## Caveats

- Training needs at least 10 valid rows; the target column must exist in the
  result set — otherwise training fails with a clear error.
- You author and explain the statement; the user runs it in a worksheet. Do not
  claim the model is trained until they run it.
