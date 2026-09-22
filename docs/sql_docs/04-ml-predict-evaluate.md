# 04 — ML Prediction & Evaluation

> `ML_PREDICT` in SQL, the prediction API, batch prediction over a query, and the metrics training produces.

Sources: `backend/app/modules/ml_engine/service.py`, `internal_router.py`, `router.py`, `backend/app/common/ml_intercept.py`, `docker/init-nova.sql`.

---

## Prediction surfaces

| Surface | How it runs | Where |
|---------|-------------|-------|
| `ML_PREDICT(alias, expressions...)` through Nova SQL | Balanced parsing, one feature query, one vectorized model call, projection-preserving result | `common/ml_intercept.py`, `query/service.py` |
| `ML_FORECAST(...)` through Nova SQL | Persisted horizon forecast returning future rows | `common/ml_intercept.py`, `query/service.py` |
| HTTP API | Single/batch prediction and alias/version forecast endpoints | `ml_engine/router.py` |

---

## `ML_PREDICT` — the SQL function

Nova's Query API, worksheet, and MySQL proxy intercept this syntax before it
reaches StarRocks:

```sql
SELECT
  customer_id,
  ML_PREDICT(
    'production_churn',
    COALESCE(total_spend, 0),
    LOG(order_count + 1)
  ) AS churn
FROM analytics.customers;
```

The scanner is quote/comment aware and balances nested expressions. Nova keeps
the non-ML projection (`customer_id` above), adds hidden feature expressions to
one engine query, predicts the Arrow batch once, removes the hidden columns,
and inserts the named prediction at its requested position. The result is
bounded by `ML_SQL_RESULT_MAX_ROWS`; there is no per-row HTTP inference.

Direct connections to StarRocks do not traverse Nova's interception pipeline.
The compatibility global UDF remains an instructional fallback for those
connections.

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
3. Stream bounded Arrow batches using the caller's StarRocks identity.
4. Run preprocessing and one vectorized prediction on the bounded inference executor.
5. Convert to Python objects only at the JSON response boundary.

Response:

```json
{
  "model_alias": "churn_model",
  "model_name": "churn_model",
  "predictions": [ { "age": 34, "income": 5200, "prediction": "churned" } ],
  "total_rows": 1
}
```

Preprocessing follows the schema and imputers stored with the model artifact.

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

## Persisted forecast inference

Forecast models use horizon semantics and are intentionally separate from
row-oriented `ML_PREDICT`. Calling a forecast bundle through generic prediction
fails clearly instead of invoking `model.predict(X)` with the wrong contract.

```http
POST /api/v1/ml/forecast
Content-Type: application/json

{
  "model_alias": "sales_forecast",
  "horizon": 30,
  "confidence_level": 95,
  "database_name": "analytics"
}
```

For reproducibility, `POST /api/v1/ml/forecast/version` accepts `model_id` and
`version`. Both paths checksum and reload the persisted artifact, including
after an API process restart.

Nova SQL exposes the same task-specific semantics without overloading
`ML_PREDICT`:

```sql
SELECT * FROM ML_FORECAST(
  MODEL => 'sales_forecast',
  HORIZON => 30,
  SERIES => 'west',
  CONFIDENCE => 95
);

SELECT * FROM ML_FORECAST(
  MODEL_ID => 'model-id', VERSION => 2, HORIZON => 30
);
```

The result columns are `timestamp`, `series`, `prediction`, `lower`, and
`upper`.

These metrics are stored as JSON in `NOVA_SYSTEM.ML_MODEL_VERSIONS.metrics` and surfaced by `GET /api/v1/ml/models/{id}` and by the `CREATE ML_MODEL` response.

---

## Aliases

A prediction resolves through `NOVA_SYSTEM.ML_MODEL_ALIASES` (`alias_name → model_id, version`), so a deployment can pin an alias to a specific version while the model accumulates newer ones.

| Action | Surface |
|--------|---------|
| List | `GET /api/v1/ml/aliases` |
| Create/update | `POST /api/v1/ml/aliases` (`alias_name`, `model_id`, `version`) |
| Delete | `DELETE /api/v1/ml/aliases/{alias_name}` |

Alias upsert targets the scoped Primary Key table; `delete_model` removes aliases
for the model.

---

## Limitations

- SQL interception requires a Nova connection; a direct StarRocks connection
  sees only the compatibility UDF.
- SQL prediction results are deliberately capped rather than materializing
  unbounded Python row arrays.
- A model trained on fewer than 20 rows evaluates on its training data.
- Internal prediction requires both a trusted peer and `X-Nova-Internal-Token`.
- Azure/GCS storage backends are not implemented, so `@stage` in a training/prediction SQL only resolves for S3-compatible connections today.

---

## Verification

| Claim | Test |
|-------|------|
| Nested SQL prediction and projection preservation | `tests/unit/test_ml_boundary_hardening.py` |
| Alias/version SQL forecast semantics | `tests/unit/test_ml_boundary_hardening.py` |
| Batch prediction prepares caller SQL (guard/translate) | `tests/unit/test_ml_engine_batch_predict.py` |
| Training SQL guard + stage translation | `tests/unit/test_ml_engine_training_sql.py` |
| `CREATE ML_MODEL` response columns/metrics shape | `tests/unit/test_ml_model_ddl.py::test_query_service_routes_create_ml_model_to_ml_engine` |
| `ML_PREDICT` UDF registration and grants | `docker/init-nova.sql`; re-registered by `llm_functions/service.py::_grant_udf_privileges` |
