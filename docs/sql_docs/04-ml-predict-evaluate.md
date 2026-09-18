# 04 — ML Prediction & Evaluation

> `ML_PREDICT` in SQL, the prediction API, batch prediction over a query, and the metrics training produces.

Sources: `backend/app/modules/ml_engine/service.py`, `internal_router.py`, `router.py`, `backend/app/common/ml_intercept.py`, `docker/init-nova.sql`.

---

## Two prediction surfaces

| Surface | How it runs | Where |
|---------|-------------|-------|
| `ML_PREDICT(alias, features_json)` in SQL | A **global UDF** registered in StarRocks. The shipped placeholder returns a hint string; production wiring calls the Nova prediction API. | `init-nova.sql:779` |
| HTTP API | `POST /api/v1/ml/predict`, `POST /api/v1/ml/predict/batch` | `ml_engine/router.py` |

---

## `ML_PREDICT` — the SQL function

Registered at init and re-registered by the backend on startup:

```sql
CREATE GLOBAL FUNCTION ML_PREDICT(model_alias STRING, features_json STRING)
RETURNS CONCAT('Use POST /api/v1/ml/predict with {"model_alias":"', model_alias,
               '","features":', features_json, '} to get prediction');
```

Its signature is `(STRING, STRING)` in the shipped form, and `GRANT USAGE` is issued to `root`, `db_admin`, `cluster_admin`, `user_admin`, and `ACCOUNTADMIN` — for the `STRING`, `VARCHAR`, and `VARCHAR(65533)` signatures — so it behaves like a native built-in for every user (`llm_functions/service.py:361`).

Example call:

```sql
SELECT ML_PREDICT('churn_model', '{"age": 34, "income": 5200}') FROM NOVA_DEMO.orders LIMIT 1;
```

> **Current behaviour:** the UDF in the repository returns an instructional string, not a prediction. Actual inference is via the HTTP API or the internal endpoint. Do not present `ML_PREDICT` as producing a model score in the current build.

### The `ml_predict()` intercept helper

`backend/app/common/ml_intercept.py` contains a helper that detects an `ml_predict('alias', …)` call, extracts the model alias and the feature arguments, and rewrites the statement to fetch the feature rows. It supports two calling shapes:

1. `ml_predict('alias', col1, col2, …)` — features as columns.
2. `ml_predict('alias', json_string)` — features as a JSON string.

The rewrite replaces the call with `NULL AS __ml_prediction__` and returns `(alias, inner_sql, feature_args)`. This module is a building block; the shipped SQL UDF does not use it.
### Internal predict endpoint

StarRocks BEs can call Nova over the authenticated internal channel:

```
POST /api/v1/internal/ml/predict     (ml_engine/internal_router.py)
```

It accepts a `PredictRequest` (`model_alias`, `features`) and delegates to
`MLEngineService.predict`. It is a machine-to-machine surface for callers with
no Nova session, so it is gated by two independent checks
(`ml_engine/internal_auth.py`):

1. The TCP peer must be loopback (`127.0.0.0/8`, `::1`) or a proxy named in
   `NOVA_INTERNAL_TRUSTED_PROXY`; anything else is rejected with `403`.
2. The request must carry `X-Nova-Internal-Token` equal to `NOVA_INTERNAL_TOKEN`;
   a missing or wrong token is rejected with `401`.

If `NOVA_INTERNAL_TOKEN` is unset the endpoint fails closed (`503`). The
previously-declared unauthenticated duplicate at `/api/v1/ml/internal/predict`
was removed (NOVA-90).


---

## Single prediction

`POST /api/v1/ml/predict`

Request:

```json
{ "model_alias": "churn_model", "features": { "age": 34, "income": 5200 } }
```

`MLEngineService.predict` (`ml_engine/service.py:340`):

1. Resolve the alias to `(model_id, version, model_name, model_type)` via `ML_MODEL_ALIASES ⋈ ML_MODELS`. Missing alias → `ValueError("Model alias '<x>' not found")`.
2. Load the base64 joblib bundle from `ML_MODEL_VERSIONS`.
3. Build the feature vector in the model's stored feature order. Missing feature → `ValueError("Missing feature column: <col>")`.
4. Predict; decode the label with the stored encoder if classification.
5. If the model exposes `predict_proba`, include a per-class probability map.

Response:

```json
{
  "model_alias": "churn_model",
  "model_name": "churn_model",
  "prediction": "churned",
  "probability": { "retained": 0.21, "churned": 0.79 },
  "model_version": 1
}
```

`probability` is `null` for models without `predict_proba` (e.g. `svm` without probability, or regressors).

---

## Batch prediction

`POST /api/v1/ml/predict/batch`

Request:

```json
{
  "model_alias": "churn_model",
  "prediction_sql": "SELECT age, income FROM NOVA_DEMO.customers",
  "database_name": "NOVA_DEMO"
}
```

`MLEngineService.batch_predict` (`ml_engine/service.py:415`):

1. Prepare `prediction_sql` through the shared pipeline — guard, parse, `@stage` translation, credential injection, redaction. This SQL runs on a credential-bearing connection, so it takes the same five steps as training (`NOVA-28`).
2. Resolve the alias and load the model.
3. Execute the query (setting `USE <database>` first when a database is given) and fetch rows.
4. Build `X`, skipping rows with NULL features; predict.
5. Return each valid input row plus a `prediction` field.

Response:

```json
{
  "model_alias": "churn_model",
  "model_name": "churn_model",
  "predictions": [ { "age": 34, "income": 5200, "prediction": "churned" } ],
  "total_rows": 1
}
```

Rows with a NULL feature are silently dropped from the output, so `total_rows` can be less than the query's row count.

---

## Evaluation metrics

Computed in `train_model` on the held-out split. When there are fewer than 20 rows (or `test_size=0`), the "test" set is the training set — metrics are optimistic and should not be read as generalization estimates.

### Classification

```json
{
  "accuracy": 0.91,
  "classification_report": {
    "retained": { "precision": …, "recall": …, "f1-score": …, "support": … },
    "churned":  { "precision": …, "recall": …, "f1-score": …, "support": … },
    "accuracy": …,
    "macro avg": { … },
    "weighted avg": { … }
  }
}
```

`classification_report` is computed with `zero_division=0` and uses the original label names when a `LabelEncoder` was fitted.

### Regression

```json
{ "mse": …, "rmse": …, "mae": …, "r2": … }
```

`rmse` is `sqrt(mse)`.

These metrics are stored as JSON in `NOVA_SYSTEM.ML_MODEL_VERSIONS.metrics` and surfaced by `GET /api/v1/ml/models/{id}` and by the `CREATE ML_MODEL` response.

---

## Aliases

A prediction resolves through `NOVA_SYSTEM.ML_MODEL_ALIASES` (`alias_name → model_id, version`), so a deployment can pin an alias to a specific version while the model accumulates newer ones.

| Action | Surface |
|--------|---------|
| List | `GET /api/v1/ml/aliases` |
| Create/update | `POST /api/v1/ml/aliases` (`alias_name`, `model_id`, `version`) |
| Delete | `DELETE /api/v1/ml/aliases/{alias_name}` |

Alias upsert is an `INSERT` into a Duplicate Key table; `delete_model` removes aliases for the model.

---

## Limitations

- `ML_PREDICT` as shipped is a placeholder, not a scorer.
- Batch prediction drops rows with any NULL feature rather than imputing.
- Feature coercion is `float()`; non-numeric categorical features are not handled (only the target label is encoded).
- A model trained on fewer than 20 rows evaluates on its training data.
- The internal predict endpoints are unauthenticated. They must remain bound to localhost; do not route them publicly.
- Azure/GCS storage backends are not implemented, so `@stage` in a training/prediction SQL only resolves for S3-compatible connections today.

---

## Verification

| Claim | Test |
|-------|------|
| Batch prediction prepares caller SQL (guard/translate) | `tests/unit/test_ml_engine_batch_predict.py` |
| Training SQL guard + stage translation | `tests/unit/test_ml_engine_training_sql.py` |
| `CREATE ML_MODEL` response columns/metrics shape | `tests/unit/test_ml_model_ddl.py::test_query_service_routes_create_ml_model_to_ml_engine` |
| `ML_PREDICT` UDF registration and grants | `docker/init-nova.sql`; re-registered by `llm_functions/service.py::_grant_udf_privileges` |
