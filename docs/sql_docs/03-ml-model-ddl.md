# 03 — `CREATE ML_MODEL` DDL

> Nova's model-training statement. It is parsed in Python, trained by the ML engine, and **never sent to StarRocks** in its written form.

Sources: `backend/app/modules/query/dialect/ml_model.py`, `backend/app/modules/query/service.py`, `backend/app/modules/ml_engine/service.py`.

---

## Concept

Classical ML models (scikit-learn) are trained from a SQL query's result set. The user declares the model, its problem type, its target column, and the training query; Nova fetches the rows, trains, evaluates, serializes the model, and stores it in `NOVA_SYSTEM`.

The DDL is intercepted in `QueryService.execute` (`service.py:234`): `is_create_ml_model(normalized_sql)` routes to `_execute_create_ml_model`, which calls `MLEngineService.train_model`. The regular StarRocks repository is never used for this statement (asserted by `test_ml_model_ddl.py::test_query_service_routes_create_ml_model_to_ml_engine`).

---

## Grammar (v1)

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

- `<model_name>` accepts an identifier or a backticked identifier.
- Clauses may appear on one line or across lines; matching is case-insensitive and `DOTALL`.
- The training query must start with `SELECT`.
- `TYPE` and `TARGET` are required. `ALGORITHM`, `TEST_SIZE`, `FEATURES`, `HYPERPARAMETERS` are optional.
- `TEST_SIZE` must satisfy `0 ≤ x < 1`; outside that range raises `ValueError("CREATE ML_MODEL TEST_SIZE must be >= 0 and < 1")`.
- `HYPERPARAMETERS` must be a JSON **object**; invalid JSON raises `ValueError("Invalid HYPERPARAMETERS JSON: …")`, a non-object raises `ValueError("CREATE ML_MODEL HYPERPARAMETERS must be a JSON object")`.
- `FEATURES` must list at least one column.

The parsed result is a frozen `CreateMLModelStatement` (`ml_model.py:41`) with `algorithm='auto'`, `test_size=0.2`, and `feature_columns=None`, `hyperparameters=None` as defaults.

---

## Examples

### Minimal classification model

```sql
CREATE ML_MODEL churn_model
  TYPE = CLASSIFICATION
  TARGET = churned
  AS SELECT age, income, churned FROM customers
```

### Full form with backticks and JSON hyperparameters

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

### Training from a stage file

The training query takes the same pipeline as a worksheet statement, so `@stage` is allowed:

```sql
CREATE ML_MODEL sentiment_model
  TYPE = CLASSIFICATION
  TARGET = label
  AS SELECT review_text, label FROM @reviews.training.csv
```

The `@stage` reference is translated and credentials injected before the query runs; the **stored** `training_sql` is the redacted form. See "Credential handling" below.

---

## Error paths

| Condition | Error |
|-----------|-------|
| Missing `AS SELECT` | `Invalid CREATE ML_MODEL syntax. Expected: CREATE ML_MODEL name TYPE = CLASSIFICATION|REGRESSION TARGET = target AS SELECT ...` |
| Missing `TYPE` | `CREATE ML_MODEL requires TYPE = CLASSIFICATION or TYPE = REGRESSION` |
| Missing `TARGET` | `CREATE ML_MODEL requires TARGET = target_column` |
| `TEST_SIZE` out of range | `CREATE ML_MODEL TEST_SIZE must be >= 0 and < 1` |
| Invalid hyperparameter JSON | `Invalid HYPERPARAMETERS JSON: <msg>` |
| Hyperparameters not an object | `CREATE ML_MODEL HYPERPARAMETERS must be a JSON object` |
| `FEATURES` empty | `CREATE ML_MODEL FEATURES must include at least one column` |
| Training query returns no rows | `Training SQL returned no rows` |
| Target not in result columns | `Target column '<x>' not found in query results` |
| Fewer than 10 usable rows | `Not enough valid rows for training: <n>. Need at least 10.` |
| Algorithm unsupported for type | `Algorithm '<x>' not supported for <type>` |
| Unknown algorithm string | Fails regex match → parse falls back to the syntax error |

---

## What training does

`MLEngineService.train_model` (`ml_engine/service.py:116`):

1. **Prepare** the training SQL through the shared pipeline (guard → `@stage` translation → credential injection → redaction). The engine form is used to fetch rows; the redacted form is what gets stored.
2. **Fetch** rows on the user's connection if `username`/`password` are supplied (so StarRocks RBAC applies), otherwise on the system connection. Worksheet-triggered training always uses the user's connection.
3. **Determine features**: if `FEATURES` was omitted, every returned column except the target.
4. **Build `X`/`y`**, skipping rows with a NULL feature or NULL target. Non-numeric features are coerced with `float()`.
5. **Encode labels** for classification when the target is non-numeric (`LabelEncoder`).
6. **Pick the algorithm** when `ALGORITHM=auto` (see below).
7. **Split** train/test with `random_state=42`, stratified for classification — but only when `0 < test_size` **and** there are at least 20 rows; otherwise the model trains and evaluates on the same data.
8. **Train**, predict on the test split, compute metrics.
9. **Serialize** the model *and* the feature list, target, and any label encoder as one joblib bundle, base64-encoded.
10. **Store** metadata in `ML_MODELS` and the binary + metrics in `ML_MODEL_VERSIONS`.

### Algorithm auto-selection

`_pick_algorithm` (`ml_engine/service.py:77`):

| Type | < 1000 rows | < 10000 rows | ≥ 10000 rows |
|------|-------------|--------------|--------------|
| classification | `decision_tree` | `random_forest` | `gradient_boost` |
| regression | `linear` | `random_forest` | `gradient_boost` |

Supported algorithms per type:

- Classification: `linear`/`logistic` (both `LogisticRegression`), `decision_tree`, `random_forest`, `gradient_boost`, `knn`, `svm`.
- Regression: `linear`, `decision_tree`, `random_forest`, `gradient_boost`, `knn`, `svm`.

---

## Metadata written

### `NOVA_SYSTEM.ML_MODELS` (one row)

| Column | Value |
|--------|-------|
| `model_id` | New UUID. |
| `model_type` | `classification` / `regression`. |
| `model_name` | The declared name. |
| `target_column` | Declared target. |
| `feature_columns` | JSON array. |
| `hyperparameters` | JSON object (or `{}`). |
| `training_sql` | **Redacted** training query. |
| `database_name` | Request database context. |
| `created_at` / `updated_at` | `NOW()`. |
| `created_by` | Authenticated username. |

### `NOVA_SYSTEM.ML_MODEL_VERSIONS` (one row)

| Column | Value |
|--------|-------|
| `version` | `COALESCE(MAX(version),0)+1` for this model (starts at 1). |
| `status` | `active`. |
| `training_rows` | Number of usable rows. |
| `metrics` | JSON metrics object. |
| `model_binary` | base64 joblib bundle. |

`ML_MODELS` and `ML_MODEL_VERSIONS` are Duplicate Key tables (append-only); `CREATE ML_MODEL` always appends. Versioning lets the same model name accumulate versions.

---

## Response

`_execute_create_ml_model` returns a one-row `QueryResult` with these columns (`service.py:546`):

```
model_id, model_name, model_type, algorithm, version, status,
training_rows, feature_columns, metrics
```

`feature_columns` and `metrics` are JSON-encoded strings in the row. On success the audit row records `object_type="ml_model"`, `object_name=<model_name>`, `status="SUCCESS"`, with `rewritten_sql` set to the normalized statement. On failure the same audit shape is written with `status="ERROR"` and the exception is re-raised.

---

## Credential handling

Training SQL is a `@stage`-capable statement executed on a credential-bearing connection, so it takes all four pipeline steps (`NOVA-28`). Critical points:

- The engine receives the credential-bearing form (`engine_sql`).
- `NOVA_SYSTEM.ML_MODELS.training_sql` stores the **redacted** form — no credential value ever reaches the row. Asserted by `test_ml_engine_training_sql.py::test_persistence_stores_the_redacted_form`.

---

## Related surfaces

| Action | Surface |
|--------|---------|
| Train via SQL | `CREATE ML_MODEL … AS SELECT …` (this document) |
| Train via API | `POST /api/v1/ml/train` |
| List models | `GET /api/v1/ml/models` or `SELECT … FROM NOVA_SYSTEM.ML_MODELS` |
| Model detail / versions | `GET /api/v1/ml/models/{id}` |
| Delete model | `DELETE /api/v1/ml/models/{id}` (also removes versions and aliases) |

---

## Verification

| Claim | Test |
|-------|------|
| Parser extracts name/type/target/algorithm/test_size/training_sql | `test_ml_model_ddl.py::TestCreateMLModelParser::test_parse_compact_classification` |
| Backticked identifiers, features, hyperparameters | `test_ml_model_ddl.py::test_parse_optional_features_and_hyperparameters` |
| `CREATE TABLE` is not intercepted | `test_ml_model_ddl.py::test_detect_only_create_ml_model_prefix` |
| Missing `AS SELECT` / `TYPE` / `TARGET` rejected | `test_ml_model_ddl.py::test_reject_*` |
| Query service routes to ML engine, not StarRocks | `test_ml_model_ddl.py::test_query_service_routes_create_ml_model_to_ml_engine` |
| Training SQL is guarded and `@stage`-translated | `test_ml_engine_training_sql.py::TestTrainingSqlIsGuarded`, `::TestTrainingSqlStageTranslation` |
| Persisted training_sql is redacted | `test_ml_engine_training_sql.py::test_persistence_stores_the_redacted_form` |
