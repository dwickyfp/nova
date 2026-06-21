# Native ML/AI di Nova — Complete Implementation Guide

> **Status**: Deep Research Complete · Ready for Implementation
> **Last Updated**: 2026-06-21
> **Authors**: Deep Research (Snowflake, BigQuery ML, Redshift ML, Databricks, DuckDB, ClickHouse, StarRocks 4.1.1)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Cross-Platform Research](#2-cross-platform-research)
3. [StarRocks Native Capabilities](#3-starrocks-native-capabilities)
4. [Nova Current State & Gap Analysis](#4-nova-current-state--gap-analysis)
5. [Architecture Design](#5-architecture-design)
6. [Phase 1: LLM Function Wrappers](#6-phase-1-llm-function-wrappers)
7. [Phase 2: Classical ML Engine](#7-phase-2-classical-ml-engine)
8. [Phase 3: Database Explorer Integration](#8-phase-3-database-explorer-integration)
9. [Phase 4: Training Wizard UI](#9-phase-4-training-wizard-ui)
10. [Phase 5: Model Monitoring & Observability](#10-phase-5-model-monitoring--observability)
11. [SQL Dialect Reference](#11-sql-dialect-reference)
12. [API Endpoints Reference](#12-api-endpoints-reference)
13. [Database Schema Reference](#13-database-schema-reference)
14. [Implementation Timeline](#14-implementation-timeline)
15. [Key Design Decisions](#15-key-design-decisions)
16. [Appendix: Platform Comparison Matrix](#16-appendix-platform-comparison-matrix)

---

## 1. Executive Summary

### Vision

Nova akan menjadi **Snowflake-grade ML/AI console** untuk StarRocks — di mana machine learning models terasa seperti **native database objects**. User bisa:

- **Train** model via SQL query (`CREATE ML_MODEL ... AS SELECT`)
- **Predict** via SQL function (`ML_PREDICT('model_name', col1, col2)`)
- **Evaluate** model quality (`ML_EVALUATE('model_name')`)
- **Browse** models di Database Explorer (bersama Tables, Views, Functions)
- **Use LLMs** langsung di SQL (`AI_COMPLETE`, `AI_SENTIMENT`, `AI_CLASSIFY`)

### Why This Matters

| Before | After |
|--------|-------|
| Export data → Python notebook → train → deploy API → call from SQL | `CREATE ML_MODEL ... AS SELECT ...` |
| Separate ML platform (MLflow, SageMaker) | Model IS a database object |
| Data scientist dependency | SQL analyst can train models |
| Weeks to production | Minutes from query to prediction |

### Design Philosophy (Learned from Industry Leaders)

1. **`CREATE MODEL` as DDL** — Training = declarative SQL, bukan procedure call
2. **Models di namespace data** — `database.model_name`, bukan ML platform terpisah
3. **Inference = function di SELECT** — Model dikonsumsi seperti SQL function
4. **Evaluation = SQL function** — Metrics accessible via SQL
5. **Metadata queryable** — Models discoverable via `SHOW ML_MODELS` + Database Explorer
6. **Remote model wrapping** — External LLMs terdaftar sebagai local function
7. **Task-specific functions** — `AI_SENTIMENT()`, `AI_CLASSIFY()` tanpa model management

---

## 2. Cross-Platform Research

### 2.1 Snowflake — The Gold Standard

Snowflake's approach: **ML models ARE database objects**, same as tables and views.

#### Cortex ML Functions (No-Code ML)

```sql
-- TRAIN: One SQL statement trains a classifier
CREATE OR REPLACE SNOWFLAKE.ML.CLASSIFICATION churn_classifier(
    INPUT_DATA     => SYSTEM$REFERENCE('VIEW', 'customer_features_view'),
    TARGET_COLNAME => 'churned',
    CONFIG_OBJECT  => {'evaluate': TRUE}
);

-- PREDICT: Model becomes a function in SELECT
SELECT
    customer_id,
    churn_classifier!PREDICT(INPUT_DATA => {*}) AS prediction
FROM customer_scoring_data;

-- EVALUATE: Call methods on the model
CALL churn_classifier!SHOW_EVALUATION_METRICS();
CALL churn_classifier!SHOW_CONFUSION_MATRIX();
CALL churn_classifier!EXPLAIN_FEATURE_IMPORTANCE();

-- BROWSE: Models in object browser
SHOW SNOWFLAKE.ML.CLASSIFICATION;
DESCRIBE SNOWFLAKE.ML.CLASSIFICATION churn_classifier;
```

#### Forecasting

```sql
CREATE OR REPLACE SNOWFLAKE.ML.FORECAST traffic_forecast(
    INPUT_DATA        => TABLE(daily_traffic_view),
    TIMESTAMP_COLNAME => 'event_date',
    TARGET_COLNAME    => 'page_views'
);

CALL traffic_forecast!FORECAST(FORECASTING_PERIODS => 30);

-- Multi-series (per store, per product)
CREATE SNOWFLAKE.ML.FORECAST store_sales(
    INPUT_DATA        => TABLE(store_sales_view),
    SERIES_COLNAME    => 'store_id',
    TIMESTAMP_COLNAME => 'sale_date',
    TARGET_COLNAME    => 'revenue'
);
```

#### Cortex LLM Functions (Built-in, Zero Config)

```sql
-- Completion
SELECT AI_COMPLETE('snowflake-arctic', 'Explain CTEs in SQL');

-- Sentiment (aspect-based)
SELECT AI_SENTIMENT(review_text, ['food', 'service', 'price'])
FROM restaurant_reviews;

-- Classification (zero-shot)
SELECT AI_CLASSIFY(
    ticket_text,
    ['billing', 'technical', 'account']
) FROM support_tickets;

-- Embeddings + RAG
SELECT AI_EMBED('snowflake-arctic-embed-l-v2.0', content) FROM documents;
```

#### Model Lifecycle

```sql
-- Versioning
ALTER MODEL churn_model SET DEFAULT_VERSION = 'v2';
ALTER MODEL churn_model VERSION v3 SET ALIAS = 'canary';

-- Promote dev → prod
CREATE MODEL prod.schema.model WITH VERSION v1
    FROM MODEL dev.schema.model VERSION v12;

-- Monitoring
CREATE MODEL MONITOR churn_monitor WITH
    MODEL = churn_model
    VERSION = 'v2'
    FUNCTION = 'predict'
    SOURCE = scoring_log_table
    REFRESH_INTERVAL = '1 day';

-- RBAC
GRANT USAGE ON MODEL churn_model TO ROLE analyst;
```

**Key Takeaway**: Snowflake collapsed the entire ML stack into the database. No MLflow, no model serving infra, no feature store. Model IS a database object.

---

### 2.2 BigQuery ML — Deepest ML-Native SQL

BigQuery: **Models are true first-class objects** alongside tables.

```sql
-- TRAIN: CREATE MODEL with AS SELECT
CREATE MODEL `mydataset.purchase_predictor`
OPTIONS(
    MODEL_TYPE = 'LOGISTIC_REG',
    INPUT_LABEL_COLS = ['will_purchase'],
    AUTO_CLASS_WEIGHTS = TRUE,
    ENABLE_GLOBAL_EXPLAIN = TRUE
) AS
SELECT os, is_mobile, country, pageviews, will_purchase
FROM `mydataset.ecommerce_data`;

-- PREDICT: Table-valued function
SELECT * FROM ML.PREDICT(
    MODEL `mydataset.purchase_predictor`,
    (SELECT * FROM `mydataset.new_customers`)
);

-- EVALUATE: Built-in metrics
SELECT * FROM ML.EVALUATE(
    MODEL `mydataset.purchase_predictor`,
    (SELECT * FROM `mydataset.test_data`)
);

-- EXPLAIN: Per-row feature attribution
SELECT * FROM ML.EXPLAIN_PREDICT(
    MODEL `mydataset.purchase_predictor`,
    (SELECT * FROM `mydataset.test_data`),
    STRUCT(3 AS top_k_features)
);

-- INTROSPECT: Weights, features, training info
SELECT * FROM ML.WEIGHTS(MODEL `mydataset.model`);
SELECT * FROM ML.FEATURE_INFO(MODEL `mydataset.model`);
SELECT * FROM ML.TRAINING_INFO(MODEL `mydataset.model`);

-- TIME SERIES
CREATE MODEL `mydataset.revenue_forecast`
OPTIONS(
    MODEL_TYPE = 'ARIMA_PLUS',
    TIME_SERIES_TIMESTAMP_COL = 'date',
    TIME_SERIES_DATA_COL = 'revenue',
    HORIZON = 30
) AS
SELECT date, revenue FROM `mydataset.daily_revenue`;

-- XGBoost with Hyperparameter Tuning
CREATE MODEL `mydataset.churn_xgb`
OPTIONS(
    MODEL_TYPE = 'BOOSTED_TREE_CLASSIFIER',
    INPUT_LABEL_COLS = ['churned'],
    NUM_TRIALS = 10,
    HPARAM_TUNING_OBJECTIVES = ['ROC_AUC'],
    LEARN_RATE = HPARAM_RANGE(0.001, 0.3)
) AS SELECT * FROM `mydataset.churn_data`;

-- REMOTE LLM: Wrap Gemini as model object
CREATE MODEL `mydataset.gemini`
REMOTE WITH CONNECTION `us.llm-connection`
OPTIONS(ENDPOINT = 'gemini-2.5-pro');

SELECT * FROM ML.GENERATE_TEXT(
    MODEL `mydataset.gemini`,
    (SELECT CONCAT('Sentiment: ', review) AS prompt FROM reviews),
    STRUCT(0.2 AS temperature, TRUE AS flatten_json_output)
);

-- METADATA
SELECT * FROM `project.dataset`.INFORMATION_SCHEMA.ML_MODELS;
```

**Supported**: 22 model types including LINEAR_REG, LOGISTIC_REG, KMEANS, XGBOOST, ARIMA_PLUS, TENSORFLOW, ONNX, PCA, AUTOML.

**Key Takeaway**: Richest algorithm support + explainability + hyperparameter tuning, all in SQL.

---

### 2.3 Redshift ML — Model Becomes a SQL Function

Redshift's elegant pattern: **CREATE MODEL creates a callable SQL function**.

```sql
-- TRAIN: Model + function created together
CREATE MODEL customer_churn
FROM customer_activity
    TARGET churn
    FUNCTION predict_churn
    IAM_ROLE default
    SETTINGS (S3_BUCKET 'my-bucket');

-- PREDICT: Just call the function
SELECT
    customer_id,
    predict_churn(state, area_code, avg_spend, calls) AS will_churn
FROM customer_activity;

-- SHOW: Model metadata
SHOW MODEL customer_churn;
-- Model State: READY
-- validation:F1: 0.855
-- Function Name: predict_churn
-- Function Parameters: state area_code avg_spend calls

-- RBAC
GRANT EXECUTE ON FUNCTION predict_churn TO marketing_role;
```

**Key Takeaway**: The "model → function" pattern is elegant but weak on lifecycle management (no versioning, manual rebuild for updates).

---

### 2.4 Databricks — Best Zero-Config AI Functions

```sql
-- Universal inference via ai_query()
SELECT ai_query(
    'churn-endpoint',
    named_struct('age', age, 'income', income),
    returnType => 'BOOLEAN'
) AS will_churn FROM customers;

-- Zero-config task-specific functions
SELECT ai_classify(text, '["billing","technical"]') FROM tickets;
SELECT ai_extract(text, named_struct('company', 'STRING', 'amount', 'DOUBLE')) FROM invoices;
SELECT ai_summarize(long_text) FROM documents;

-- Forecasting as table function
SELECT * FROM ai_forecast(
    TABLE daily_sales,
    horizon => 30,
    time_col => 'date',
    value_col => 'revenue'
);
```

**Key Takeaway**: Best zero-config LLM functions. Training still requires Python/MLflow.

---

### 2.5 DuckDB & ClickHouse — Embedded Approaches

**DuckDB (infera extension)**:
```sql
SELECT infera_load_model('model', 'path/to/model.onnx');
SELECT infera_predict('model', feature1, feature2) FROM data;
```

**ClickHouse (aggregate functions)**:
```sql
-- Model trained via aggregation
CREATE TABLE model AS
SELECT stochasticLinearRegression(0.01, 0.1, 15, 'Adam')(target, x1, x2) AS state
FROM train_data;

-- Predict via evalMLMethod
SELECT evalMLMethod(state, x1, x2) FROM model CROSS JOIN test_data;
```

---

### 2.6 Universal Pattern Summary

```
┌──────────────────────────────────────────────────────────────┐
│              THE "ML-AS-SQL" DESIGN PATTERN                  │
│                                                              │
│  ┌─────────┐    ┌──────────────┐    ┌─────────────────┐     │
│  │ TRAIN   │───►│  MODEL       │───►│ PREDICT         │     │
│  │ CREATE  │    │  (DB Object) │    │ SELECT model(…) │     │
│  │ MODEL   │    │              │    │ FROM data       │     │
│  │ AS SELECT│   │  ├ metadata  │    │                 │     │
│  └─────────┘    │  ├ artifact  │    └─────────────────┘     │
│                 │  └ versions  │                             │
│  ┌─────────┐    └──────┬───────┘    ┌─────────────────┐     │
│  │ EVALUATE│◄──────────┘            │ BROWSE          │     │
│  │ ML_EVAL │                        │ SHOW ML_MODELS  │     │
│  │ (model) │                        │ Database Tree   │     │
│  └─────────┘                        └─────────────────┘     │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. StarRocks Native Capabilities

### 3.1 What StarRocks 4.1.1 Has

| Capability | Details | Status |
|---|---|---|
| **`ai_query()`** | `ai_query(prompt, config_json) → VARCHAR`. Async, thread pool, LRU cache, de-duplication. Supports OpenAI, Anthropic, any OpenAI-compatible. | ✅ Built-in |
| **Vector distance** | `cosine_similarity`, `approx_cosine_similarity`, `approx_l2_distance` | ✅ Built-in |
| **Vector index** | IVFPQ + HNSW on `ARRAY<FLOAT>` columns. Requires `enable_experimental_vector = true`. | ✅ Available |
| **Python UDF** | Scalar UDFs. Supports sklearn/pandas/numpy via gRPC. `arrow` input mode for vectorized batches. | ✅ Available (experimental) |
| **Java UDF** | Scalar + UDAF + UDTF. JNI bridge. Can bundle ONNX Runtime, DJL for ML inference. | ✅ Mature |
| **SQL UDF** | Expression macros expanded at optimization time. Zero overhead. | ✅ Available |
| **`enable_udf`** | Confirmed `true` on our FE | ✅ Enabled |

### 3.2 What StarRocks Does NOT Have

| Missing | Competitor |
|---|---|
| `CREATE MODEL` / `ML_PREDICT` | BigQuery ML, Databricks |
| `FORECAST`, `CLASSIFICATION`, `ANOMALY_DETECTION` | Snowflake Cortex ML |
| Native in-database training | BigQuery ML |
| Model registry / versioning | MLflow, Snowflake |
| `ai_sentiment`, `ai_classify` (built-in) | Alibaba StarRocks Enterprise only |
| External model serving protocol | — |

### 3.3 The `ai_query()` Function (Deep Dive)

```sql
-- Basic usage
SELECT ai_query(
    'Classify sentiment: This product is amazing!',
    '{"model": "gpt-4o-mini", "api-key": "env.OPENAI_API_KEY"}'
);

-- With provider config from CONFIG_AI_PROVIDERS
-- (Nova backend resolves provider → injects config)
SELECT ai_query(
    CONCAT('Rate 1-5: ', review_text),
    '{"model": "gpt-4o-mini", "api-key": "sk-...", "max-tokens": 10}'
) AS rating
FROM product_reviews;
```

**Architecture**: `ai_query()` → `LLMQueryService` (C++, async thread pool) → `LLMCache` (LRU) → HTTP call to provider.

**Config knobs** (FE config):
- `llm_max_queue_size` (default: 100)
- `llm_max_concurrent_queries` (default: 10)
- `llm_cache_size` (default: 65536)

### 3.4 Python UDF for ML Inference

```sql
-- Create a Python UDF that loads a sklearn model
CREATE FUNCTION ml_predict_churn(features STRING)
RETURNS STRING
TYPE = 'Python'
SYMBOL = 'churn_model.predict'
FILE = 'http://minio:9000/nova-stages/models/churn_model.py.zip'
INPUT = 'arrow'
AS $$
import pickle, pyarrow as pa, numpy as np

# Model loaded once at module level (cached)
_model = None

def load_model():
    global _model
    if _model is None:
        _model = pickle.load(open('/tmp/churn.pkl', 'rb'))
    return _model

def predict(features_batch: pa.Array) -> pa.Array:
    model = load_model()
    X = np.array([json.loads(f) for f in features_batch.to_pylist()])
    predictions = model.predict(X)
    return pa.array([str(p) for p in predictions])
$$;
```

**Limitation**: Python UDFs require Python 3.8+ installed on BE nodes with `pyarrow`, `grpcio`. This needs Docker image modification.

---

## 4. Nova Current State & Gap Analysis

### 4.1 What's Already Implemented ✅

#### LLM Provider Management (Fully Working)

| Component | Status | Details |
|---|---|---|
| `CONFIG_AI_PROVIDERS` table | ✅ LIVE | Provider CRUD (name, type, endpoint, api_key) |
| `CONFIG_AI_MODELS` table | ✅ LIVE | Model CRUD under providers |
| Backend `ai_ml` module | ✅ LIVE | 9 endpoints at `/api/v1/ai/` |
| Frontend AI Providers page | ✅ LIVE | CRUD + Test Connection + dropdown menus |
| `ai_query()` in StarRocks | ✅ LIVE | Built-in function |

#### API Endpoints (Existing)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/ai/providers` | List providers |
| `POST` | `/api/v1/ai/providers` | Create provider |
| `PUT` | `/api/v1/ai/providers/{id}` | Update provider |
| `DELETE` | `/api/v1/ai/providers/{id}` | Delete + cascade |
| `POST` | `/api/v1/ai/test-connection` | Test LLM reachability |
| `GET` | `/api/v1/ai/providers/{id}/models` | List models |
| `POST` | `/api/v1/ai/providers/{id}/models` | Create model |
| `PUT` | `/api/v1/ai/models/{id}` | Update model |
| `DELETE` | `/api/v1/ai/models/{id}` | Delete model |

### 4.2 What Exists But Has Issues ⚠️

#### ML Registry Tables (Schema Mismatch)

```sql
-- CURRENT (init-nova.sql): DUPLICATE KEY (append-only, no UPDATE/DELETE)
CREATE TABLE ML_MODELS (...) DUPLICATE KEY (model_id, model_type);

-- NEEDED: PRIMARY KEY (supports UPDATE, DELETE, upsert)
CREATE TABLE ML_MODELS (...) PRIMARY KEY (model_id);
```

**Tables affected**: `ML_MODELS`, `ML_MODEL_VERSIONS`, `ML_MODEL_ALIASES`

### 4.3 What's Completely Missing ❌

```
┌─────────────────────────────────────────────────────────────┐
│                    COMPLETE GAP MAP                          │
├───────────────────────────┬─────────────────────────────────┤
│ Gap                       │ What Needs to Be Built          │
├───────────────────────────┼─────────────────────────────────┤
│                           │                                 │
│ LLM WRAPPERS              │                                 │
│  AI_COMPLETE()            │ SQL UDF wrapping ai_query()     │
│  AI_SENTIMENT()           │ SQL UDF + system prompt         │
│  AI_CLASSIFY()            │ SQL UDF + categories param      │
│  AI_SUMMARIZE()           │ SQL UDF + summarization prompt  │
│  AI_EXTRACT()             │ SQL UDF + JSON schema param     │
│  AI_TRANSLATE()           │ SQL UDF + language params       │
│  AI_FILTER()              │ SQL UDF + boolean classification│
│  AI_EMBED()               │ Python UDF (returns ARRAY<FLOAT>)│
│  Model Alias Registry     │ Backend service + table         │
│                           │                                 │
│ CLASSICAL ML              │                                 │
│  MLEngine service         │ Python ML orchestrator          │
│  train_classification()   │ XGBoost/GradientBoosting        │
│  train_forecast()         │ Prophet/ARIMA                   │
│  train_anomaly()          │ IsolationForest                 │
│  ML_PREDICT() function    │ Python UDF + sklearn            │
│  ML_FORECAST() function   │ Python UDF + Prophet            │
│  ML_EVALUATE() function   │ Python UDF + sklearn.metrics    │
│  ML_FEATURE_IMPORTANCE()  │ Python UDF + model.coef_        │
│  Model artifact storage   │ MinIO stage integration         │
│  Training data pipeline   │ SQL → DataFrame → train         │
│                           │                                 │
│ SQL DIALECT               │                                 │
│  CREATE ML_MODEL parser   │ Intercept + route to MLEngine   │
│  SHOW ML_MODELS           │ Query ML_MODELS table           │
│  DROP ML_MODEL            │ Delete model + artifact         │
│  ALTER ML_MODEL           │ Version management              │
│                           │                                 │
│ UI                        │                                 │
│  ML Models tree node      │ Database Explorer group         │
│  Model detail panel       │ Metrics + versions + importance │
│  Training wizard          │ Visual model creation           │
│  Prediction playground    │ Test model with sample data     │
└───────────────────────────┴─────────────────────────────────┘
```

---

## 5. Architecture Design

### 5.1 The Nova ML Stack

```
┌─────────────────────────────────────────────────────────────────────┐
│                          USER LAYER                                 │
│                                                                     │
│   ┌──────────────┐    ┌──────────────────┐    ┌────────────────┐   │
│   │ SQL Worksheet │    │ Database Explorer│    │ ML Manager     │   │
│   │              │    │                  │    │                │   │
│   │ SELECT       │    │ └ NOVA_DEMO     │    │ Model List     │   │
│   │   ML_PREDICT(│    │   ├ Tables      │    │ Training       │   │
│   │    'churn',  │    │   ├ Views       │    │ Wizard         │   │
│   │    age, $)   │    │   ├ Functions   │    │ Prediction     │   │
│   │ FROM ...     │    │   └ ML Models ◄─┼────│ Playground     │   │
│   └──────┬───────┘    └────────┬─────────┘    └───────┬────────┘   │
│          │                     │                       │            │
├──────────┼─────────────────────┼───────────────────────┼────────────┤
│          │           API LAYER │                       │            │
│          ▼                     ▼                       ▼            │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    NOVA BACKEND (FastAPI)                    │   │
│   │                                                             │   │
│   │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │   │
│   │  │ SQL Dialect  │  │ ML Service   │  │ LLM Alias        │  │   │
│   │  │ Parser       │  │              │  │ Service          │  │   │
│   │  │              │  │ - train()    │  │                  │  │   │
│   │  │ - CREATE     │  │ - predict()  │  │ - resolve()      │  │   │
│   │  │   ML_MODEL   │  │ - evaluate() │  │ - register_udf() │  │   │
│   │  │ - SHOW       │  │ - explain()  │  │                  │  │   │
│   │  │   ML_MODELS  │  │ - monitor()  │  │                  │  │   │
│   │  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │   │
│   │         │                  │                    │            │   │
│   │  ┌──────┴──────────────────┴────────────────────┴─────────┐  │   │
│   │  │                    ML ENGINE (Python)                   │  │   │
│   │  │                                                        │  │   │
│   │  │  ClassificationEngine  │  ForecastEngine  │  AnomalyEngine │
│   │  │  (XGBoost/GBM)         │  (Prophet/ARIMA) │  (IsolationForest)│
│   │  │                                                        │  │   │
│   │  │  Preprocessor │ Serializer │ Evaluator │ FeatureEncoder │  │   │
│   │  └────────────────────────────────────────────────────────┘  │   │
│   └──────────────────────────────────┬───────────────────────────┘   │
│                                      │                              │
├──────────────────────────────────────┼──────────────────────────────┤
│                          DATA LAYER  │                              │
│                                      ▼                              │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                   STARROCKS 4.1.1                           │   │
│   │                                                             │   │
│   │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │   │
│   │  │ NOVA_SYSTEM  │  │ ai_query()   │  │ Python UDFs      │  │   │
│   │  │              │  │              │  │                  │  │   │
│   │  │ ML_MODELS    │  │ LLM wrappers │  │ ML_PREDICT()     │  │   │
│   │  │ ML_VERSIONS  │  │ (SQL UDF)    │  │ ML_FORECAST()    │  │   │
│   │  │ ML_RUNS      │  │              │  │ ML_EVALUATE()    │  │   │
│   │  │ CONFIG_AI_*  │  │              │  │                  │  │   │
│   │  └──────────────┘  └──────────────┘  └──────────────────┘  │   │
│   └──────────────────────────────────┬──────────────────────────┘   │
│                                      │                              │
│   ┌──────────────────────────────────┴──────────────────────────┐   │
│   │                   MINIO (Object Storage)                     │   │
│   │                                                             │   │
│   │  nova-ml-artifacts/                                         │   │
│   │  ├── churn_predictor/v1/model.pkl                           │   │
│   │  ├── churn_predictor/v2/model.pkl                           │   │
│   │  ├── revenue_forecast/v1/model.pkl                          │   │
│   │  └── server_anomaly/v1/model.pkl                            │   │
│   └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.2 Data Flow

#### Training Flow

```
User writes SQL:
  CREATE ML_MODEL churn_v1
  TYPE = CLASSIFICATION
  AS SELECT age, income, tenure FROM customers
  TARGET churned;
      │
      ▼
Nova SQL Parser intercepts DDL
      │
      ▼
ML Service:
  1. Execute AS SELECT → get training data from StarRocks
  2. Preprocess: encode categoricals, scale numerics, handle nulls
  3. Train: XGBoost / GradientBoosting / etc.
  4. Evaluate: accuracy, F1, precision, recall, AUC
  5. Serialize: pickle model → upload to MinIO
  6. Register: INSERT into ML_MODELS + ML_MODEL_VERSIONS
  7. Create UDF: Register Python UDF in StarRocks for inference
      │
      ▼
Response:
  {
    "model_id": "churn_v1",
    "status": "ready",
    "metrics": {"accuracy": 0.923, "f1": 0.891},
    "feature_importance": [{"name": "tenure", "score": 0.35}, ...],
    "versions": [{"version": 1, "status": "ready"}]
  }
```

#### Inference Flow

```
User writes SQL:
  SELECT customer_id,
         ML_PREDICT('churn_v1', age, income, tenure) AS prediction
  FROM customers;
      │
      ▼
StarRocks resolves ML_PREDICT UDF
      │
      ▼
Python UDF:
  1. Load model from MinIO (cached in memory)
  2. Preprocess input features (same pipeline as training)
  3. Call model.predict(X)
  4. Return predictions as column
      │
      ▼
Result set returned to user
```

### 5.3 Module Structure

```
backend/app/modules/
├── ai_ml/                    # EXISTING — Provider/Model CRUD
│   ├── router.py             # 9 endpoints at /api/v1/ai/
│   ├── service.py
│   ├── repository.py
│   └── schemas.py
│
├── ml/                       # NEW — ML Engine
│   ├── router.py             # Training, evaluation, prediction endpoints
│   ├── service.py            # Orchestrates engine + registry
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── base.py           # BaseMLEngine abstract class
│   │   ├── classification.py # XGBoost, GradientBoosting
│   │   ├── regression.py     # XGBoost, LinearRegression
│   │   ├── forecast.py       # Prophet, ARIMA, GradientBoosting
│   │   └── anomaly.py        # IsolationForest, LocalOutlierFactor
│   ├── preprocessor.py       # Feature encoding, scaling, null handling
│   ├── serializer.py         # Pickle to/from MinIO
│   ├── udf_manager.py        # Create/drop StarRocks Python UDFs
│   ├── repository.py         # ML_MODELS, ML_VERSIONS queries
│   └── schemas.py            # Pydantic models for ML
│
└── llm_functions/            # NEW — LLM Wrapper Management
    ├── router.py             # Alias CRUD + UDF registration
    ├── service.py            # Resolve aliases, register SQL UDFs
    ├── repository.py         # CONFIG_MODEL_ALIASES queries
    ├── schemas.py
    └── templates/
        ├── complete.sql      # AI_COMPLETE UDF template
        ├── sentiment.sql     # AI_SENTIMENT UDF template
        ├── classify.sql      # AI_CLASSIFY UDF template
        ├── summarize.sql     # AI_SUMMARIZE UDF template
        ├── extract.sql       # AI_EXTRACT UDF template
        └── filter.sql        # AI_FILTER UDF template
```

---

## 6. Phase 1: LLM Function Wrappers

> **Effort**: 2-3 days | **Impact**: 🔥 High | **Dependencies**: None (provider config exists)

### 6.1 Goal

SQL analyst bisa pakai LLM functions langsung di worksheet, tanpa perlu tahu provider config:

```sql
-- Simple: just call the function
SELECT AI_SENTIMENT(review_text) FROM reviews;
SELECT AI_CLASSIFY(ticket, 'billing,technical,account') FROM tickets;
SELECT AI_COMPLETE('Summarize: ' || article) FROM documents;
```

### 6.2 Model Alias Registry

**New table**: `NOVA_SYSTEM.CONFIG_MODEL_ALIASES`

```sql
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_MODEL_ALIASES (
    id VARCHAR(64),
    alias_name VARCHAR(128),           -- 'default_llm', 'sentiment_model'
    function_type VARCHAR(32),         -- 'complete', 'sentiment', 'classify', 'summarize', 'extract', 'filter', 'embed', 'translate'
    provider_id VARCHAR(64),           -- FK → CONFIG_AI_PROVIDERS
    model_id VARCHAR(64),              -- FK → CONFIG_AI_MODELS
    system_prompt TEXT,                -- Default system prompt template
    default_params VARCHAR(512),       -- JSON: {"temperature": 0.3, "max_tokens": 500}
    is_default BOOLEAN DEFAULT FALSE,  -- Default alias for this function_type
    created_at DATETIME,
    updated_at DATETIME
) PRIMARY KEY (id)
DISTRIBUTED BY HASH(id)
PROPERTIES ("replication_num" = "1");
```

**Default aliases** (seeded on first startup):

| alias_name | function_type | Purpose |
|---|---|---|
| `default_complete` | `complete` | General LLM completion |
| `default_sentiment` | `sentiment` | Sentiment analysis |
| `default_classify` | `classify` | Zero-shot classification |
| `default_summarize` | `summarize` | Text summarization |
| `default_extract` | `extract` | Entity extraction |
| `default_translate` | `translate` | Translation |
| `default_filter` | `filter` | Semantic boolean filter |

### 6.3 UDF Registration

Nova backend registers SQL UDFs on startup (and when aliases change):

```sql
-- AI_COMPLETE: General completion
CREATE OR REPLACE FUNCTION AI_COMPLETE(prompt VARCHAR)
RETURNS VARCHAR
AS "ai_query(prompt,
    CONCAT('{',
        '\"model\": \"', (SELECT m.model_name FROM NOVA_SYSTEM.CONFIG_AI_MODELS m
                          JOIN NOVA_SYSTEM.CONFIG_MODEL_ALIASES a ON a.model_id = m.id
                          WHERE a.function_type = 'complete' AND a.is_default = TRUE),
        '\", \"api-key\": \"', (SELECT p.api_key FROM NOVA_SYSTEM.CONFIG_AI_PROVIDERS p
                                 JOIN NOVA_SYSTEM.CONFIG_MODEL_ALIASES a ON a.provider_id = p.id
                                 WHERE a.function_type = 'complete' AND a.is_default = TRUE),
        '\"}'
    )
)";
```

**Simpler approach** (recommended): Backend resolves provider config at registration time and injects literal values:

```python
# llm_functions/service.py

class LLMFunctionService:
    async def register_all_udfs(self):
        """Register all LLM wrapper UDFs in StarRocks on startup."""
        aliases = await self.repository.get_default_aliases()

        for alias in aliases:
            provider = await self.ai_repo.get_provider(alias.provider_id)
            model = await self.ai_repo.get_model(alias.model_id)

            config = json.dumps({
                "model": model.model_name,
                "api-key": provider.api_key,
                **json.loads(alias.default_params or '{}')
            })

            udf_sql = self._build_udf_sql(alias.function_type, config, alias.system_prompt)
            await db.execute_system(ddl_sql)

    def _build_udf_sql(self, function_type: str, config: str, system_prompt: str) -> str:
        templates = {
            'complete': f"""
                CREATE OR REPLACE FUNCTION AI_COMPLETE(prompt VARCHAR)
                RETURNS VARCHAR
                AS "ai_query(prompt, '{config}')"
            """,
            'sentiment': f"""
                CREATE OR REPLACE FUNCTION AI_SENTIMENT(text VARCHAR)
                RETURNS VARCHAR
                AS "ai_query(
                    CONCAT('{system_prompt}', text),
                    '{config}'
                )"
            """,
            'classify': f"""
                CREATE OR REPLACE FUNCTION AI_CLASSIFY(text VARCHAR, categories VARCHAR)
                RETURNS VARCHAR
                AS "ai_query(
                    CONCAT('{system_prompt}', categories, ']: ', text),
                    '{config}'
                )"
            """,
            # ... more templates
        }
        return templates[function_type]
```

### 6.4 LLM Function Templates

#### AI_COMPLETE

```sql
-- Usage
SELECT AI_COMPLETE('What is the capital of France?') AS answer;
SELECT AI_COMPLETE(CONCAT('Translate to Indonesian: ', text)) FROM documents;

-- With table data
SELECT
    ticket_id,
    AI_COMPLETE(CONCAT('Categorize this support ticket: ', message)) AS category
FROM support_tickets;
```

#### AI_SENTIMENT

```sql
-- System prompt: "Analyze the sentiment. Reply with JSON: {\"sentiment\": \"positive|negative|neutral\", \"confidence\": 0.0-1.0}"

-- Usage
SELECT AI_SENTIMENT('The food was amazing but service was slow') AS result;
-- Returns: {"sentiment": "mixed", "confidence": 0.85}

-- Batch over table
SELECT
    review_id,
    AI_SENTIMENT(review_text) AS sentiment
FROM product_reviews;
```

#### AI_CLASSIFY

```sql
-- System prompt: "Classify the text into one of these categories: ["

-- Usage
SELECT AI_CLASSIFY(
    'My laptop keeps crashing when I open the spreadsheet app',
    'hardware,software,billing,account'
) AS category;
-- Returns: "software"

-- Batch
SELECT
    ticket_id,
    AI_CLASSIFY(message, 'billing,technical,account,feature_request') AS category
FROM support_tickets
WHERE created_at > NOW() - INTERVAL 7 DAY;
```

#### AI_SUMMARIZE

```sql
-- System prompt: "Summarize the following text concisely in 2-3 sentences:"

SELECT AI_SUMMARIZE(article_body) AS summary
FROM articles
WHERE length(article_body) > 1000;
```

#### AI_EXTRACT

```sql
-- System prompt: "Extract structured data as JSON with these fields: "

SELECT AI_EXTRACT(
    'John Smith from Acme Corp ordered 500 units at $12.50 each on 2026-03-15',
    '{"name": "string", "company": "string", "quantity": "number", "unit_price": "number", "date": "string"}'
) AS extracted;
-- Returns: {"name": "John Smith", "company": "Acme Corp", "quantity": 500, ...}
```

#### AI_FILTER

```sql
-- System prompt: "Does the following text match the criteria? Reply ONLY 'true' or 'false': "

SELECT * FROM documents
WHERE AI_FILTER(content, 'Contains confidential financial information') = 'true';
```

#### AI_TRANSLATE

```sql
-- System prompt: "Translate the following text to {target_lang}. Reply ONLY with the translation:"

SELECT AI_TRANSLATE(review_text, 'Indonesian') AS translated
FROM reviews
WHERE language_detected = 'en';
```

### 6.5 API Endpoints

```
POST   /api/v1/ai/aliases                     — Create alias
GET    /api/v1/ai/aliases                     — List all aliases
PUT    /api/v1/ai/aliases/{id}                — Update alias
DELETE /api/v1/ai/aliases/{id}                — Delete alias
POST   /api/v1/ai/aliases/register-udfs       — Re-register all UDFs in StarRocks
GET    /api/v1/ai/aliases/udf-status          — Check which UDFs are registered
```

### 6.6 Frontend: LLM Functions Section

Add to AI Providers page:

```
┌──────────────────────────────────────────────────────┐
│ AI Providers & Functions                             │
│                                                      │
│ [Providers] [Models] [Functions] ◄── NEW TAB         │
│                                                      │
│ Functions Tab:                                       │
│ ┌──────────────────────────────────────────────────┐ │
│ │ Function      │ Model Used     │ Status │ Actions│ │
│ │───────────────│────────────────│────────│────────│ │
│ │ AI_COMPLETE   │ gpt-4o-mini    │ ✅ Active│ Edit  │ │
│ │ AI_SENTIMENT  │ gpt-4o-mini    │ ✅ Active│ Edit  │ │
│ │ AI_CLASSIFY   │ gpt-4o-mini    │ ✅ Active│ Edit  │ │
│ │ AI_SUMMARIZE  │ gpt-4o-mini    │ ⚠️ Not Set│ Setup│ │
│ │ AI_EXTRACT    │ gpt-4o-mini    │ ✅ Active│ Edit  │ │
│ │ AI_TRANSLATE  │ —              │ ❌ Disabled│ Setup│ │
│ │ AI_FILTER     │ —              │ ❌ Disabled│ Setup│ │
│ └──────────────────────────────────────────────────┘ │
│                                                      │
│ Edit Dialog:                                         │
│ ┌──────────────────────────────────────────────────┐ │
│ │ Function: AI_SENTIMENT                           │ │
│ │ Provider: [9router    ▼]                         │ │
│ │ Model:    [gpt-4o-mini▼]                         │ │
│ │ System Prompt:                                   │ │
│ │ ┌──────────────────────────────────────────────┐ │ │
│ │ │ Analyze sentiment. Reply JSON:               │ │ │
│ │ │ {"sentiment": "...", "confidence": 0.0}      │ │ │
│ │ └──────────────────────────────────────────────┘ │ │
│ │ Temperature: [0.3    ]  Max Tokens: [500]        │ │
│ │ [Test] [Save]                                    │ │
│ └──────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

---

## 7. Phase 2: Classical ML Engine

> **Effort**: 1-2 weeks | **Impact**: 🔥🔥 Highest | **Dependencies**: Python on BE nodes (for UDFs)

### 7.1 Goal

User trains ML model via SQL, model becomes callable function:

```sql
-- Train
CREATE ML_MODEL churn_predictor
TYPE = CLASSIFICATION
ALGORITHM = XGBOOST
AS SELECT age, income, tenure, plan_type, support_calls
   FROM customer_features
TARGET churned;

-- Predict
SELECT customer_id, ML_PREDICT('churn_predictor', age, income, tenure, plan_type, support_calls)
FROM customers;

-- Evaluate
SELECT * FROM ML_EVALUATE('churn_predictor');
```

### 7.2 Database Schema (Fixed)

```sql
-- ============================================================
-- ML_MODELS: Model Registry (Primary Key — supports UPDATE)
-- ============================================================
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_MODELS (
    model_id VARCHAR(64),
    model_name VARCHAR(128),
    database_name VARCHAR(128),        -- Which database this model belongs to
    model_type VARCHAR(32),            -- 'classification', 'regression', 'forecast', 'anomaly'
    algorithm VARCHAR(64),             -- 'xgboost', 'gradient_boosting', 'prophet', 'isolation_forest', 'linear_regression'
    status VARCHAR(16),                -- 'training', 'ready', 'failed', 'archived'
    current_version INT DEFAULT 1,
    default_version INT DEFAULT 1,
    feature_columns VARCHAR(2048),     -- JSON array: ["age", "income", "tenure"]
    target_column VARCHAR(128),
    timestamp_column VARCHAR(128),     -- For forecast models
    series_column VARCHAR(128),        -- For multi-series forecast
    training_query TEXT,               -- The SELECT that produced training data
    training_row_count INT,
    artifact_path VARCHAR(512),        -- MinIO path: nova-ml-artifacts/{model_name}/v{N}/model.pkl
    preprocessor_path VARCHAR(512),    -- MinIO path for preprocessor pickle
    metrics VARCHAR(2048),             -- JSON: {"accuracy": 0.92, "f1": 0.89, "auc": 0.96}
    hyperparams VARCHAR(2048),         -- JSON: {"n_estimators": 200, "max_depth": 5}
    feature_importance VARCHAR(4096),  -- JSON: [{"feature": "tenure", "importance": 0.35}, ...]
    owner VARCHAR(64),
    comment TEXT,
    created_at DATETIME,
    updated_at DATETIME
) PRIMARY KEY (model_id)
DISTRIBUTED BY HASH(model_id)
PROPERTIES ("replication_num" = "1");

-- ============================================================
-- ML_MODEL_VERSIONS: Version History
-- ============================================================
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_MODEL_VERSIONS (
    model_id VARCHAR(64),
    version INT,
    algorithm VARCHAR(64),
    status VARCHAR(16),
    training_row_count INT,
    artifact_path VARCHAR(512),
    preprocessor_path VARCHAR(512),
    metrics VARCHAR(2048),
    hyperparams VARCHAR(2048),
    feature_importance VARCHAR(4096),
    feature_columns VARCHAR(2048),
    training_log TEXT,
    comment TEXT,
    created_at DATETIME,
    created_by VARCHAR(64)
) PRIMARY KEY (model_id, version)
DISTRIBUTED BY HASH(model_id)
PROPERTIES ("replication_num" = "1");

-- ============================================================
-- ML_TRAINING_RUNS: Audit Trail (Append-only)
-- ============================================================
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_TRAINING_RUNS (
    run_id VARCHAR(64),
    model_id VARCHAR(64),
    version INT,
    status VARCHAR(16),                -- 'running', 'completed', 'failed'
    started_at DATETIME,
    completed_at DATETIME,
    duration_ms INT,
    training_query TEXT,
    training_row_count INT,
    metrics VARCHAR(2048),
    error_message TEXT,
    triggered_by VARCHAR(64),
    trigger_type VARCHAR(16)           -- 'manual', 'scheduled', 'api'
) PRIMARY KEY (run_id)
DISTRIBUTED BY HASH(run_id)
PROPERTIES ("replication_num" = "1");

-- ============================================================
-- ML_PREDICTION_LOGS: Usage tracking (for monitoring)
-- ============================================================
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_PREDICTION_LOGS (
    log_id VARCHAR(64),
    model_id VARCHAR(64),
    model_version INT,
    prediction_count INT,
    avg_latency_ms FLOAT,
    called_by VARCHAR(64),
    called_at DATETIME,
    query_hash VARCHAR(64)
) DUPLICATE KEY (log_id)
DISTRIBUTED BY HASH(model_id)
PROPERTIES ("replication_num" = "1");
```

### 7.3 ML Engine Design

#### Base Engine (Abstract)

```python
# backend/app/modules/ml/engine/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
import pandas as pd

@dataclass
class TrainingResult:
    model: Any                          # The trained model object
    preprocessor: Any                   # The fitted preprocessor pipeline
    metrics: dict[str, float]           # {"accuracy": 0.92, "f1": 0.89}
    feature_importance: list[dict]      # [{"feature": "age", "importance": 0.35}]
    training_log: str                   # Human-readable training output
    feature_columns: list[str]          # Ordered feature names
    target_column: str                  # Target column name

@dataclass
class PredictionResult:
    predictions: list                   # Predicted values
    probabilities: list | None          # Prediction probabilities (classification)
    confidence: list | None             # Confidence scores

@dataclass
class EvaluationResult:
    metrics: dict[str, float]           # All evaluation metrics
    confusion_matrix: list[list] | None # For classification
    feature_importance: list[dict]      # Feature importance scores

class BaseMLEngine(ABC):
    @abstractmethod
    def train(self, df: pd.DataFrame, target: str,
              features: list[str], hyperparams: dict) -> TrainingResult:
        ...

    @abstractmethod
    def predict(self, model: Any, preprocessor: Any,
                df: pd.DataFrame) -> PredictionResult:
        ...

    @abstractmethod
    def evaluate(self, model: Any, preprocessor: Any,
                 df: pd.DataFrame, target: str) -> EvaluationResult:
        ...
```

#### Classification Engine

```python
# backend/app/modules/ml/engine/classification.py

class ClassificationEngine(BaseMLEngine):
    ALGORITHMS = {
        'xgboost': XGBClassifier,
        'gradient_boosting': GradientBoostingClassifier,
        'random_forest': RandomForestClassifier,
        'logistic_regression': LogisticRegression,
    }

    def train(self, df, target, features, hyperparams) -> TrainingResult:
        algorithm = hyperparams.pop('algorithm', 'xgboost')
        model_cls = self.ALGORITHMS[algorithm]

        # Preprocess
        preprocessor = self._build_preprocessor(df, features)
        X = preprocessor.fit_transform(df[features])
        y = df[target]

        # Train/test split
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2)

        # Train
        model = model_cls(**hyperparams)
        model.fit(X_train, y_train)

        # Evaluate
        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test) if hasattr(model, 'predict_proba') else None

        metrics = {
            'accuracy': accuracy_score(y_test, y_pred),
            'precision': precision_score(y_test, y_pred, average='weighted'),
            'recall': recall_score(y_test, y_pred, average='weighted'),
            'f1': f1_score(y_test, y_pred, average='weighted'),
        }
        if y_proba is not None:
            metrics['auc'] = roc_auc_score(y_test, y_proba, multi_class='ovr')

        # Feature importance
        importance = self._extract_feature_importance(model, features)

        return TrainingResult(
            model=model,
            preprocessor=preprocessor,
            metrics=metrics,
            feature_importance=importance,
            training_log=f"Trained {algorithm} on {len(df)} rows",
            feature_columns=features,
            target_column=target
        )
```

#### Forecast Engine

```python
# backend/app/modules/ml/engine/forecast.py

class ForecastEngine(BaseMLEngine):
    ALGORITHMS = {
        'prophet': self._train_prophet,
        'gradient_boosting': self._train_gb_timeseries,
    }

    def train(self, df, target, features, hyperparams) -> TrainingResult:
        algorithm = hyperparams.pop('algorithm', 'prophet')
        timestamp_col = hyperparams.pop('timestamp_column', 'ds')
        series_col = hyperparams.pop('series_column', None)

        if algorithm == 'prophet':
            return self._train_prophet(df, target, timestamp_col, series_col, hyperparams)
        else:
            return self._train_gb_timeseries(df, target, timestamp_col, features, hyperparams)

    def _train_prophet(self, df, target, timestamp_col, series_col, hyperparams):
        from prophet import Prophet

        # Prophet expects 'ds' and 'y' columns
        df = df.rename(columns={timestamp_col: 'ds', target: 'y'})

        model = Prophet(**hyperparams)

        # Add regressors (exogenous variables)
        for col in df.columns:
            if col not in ('ds', 'y'):
                model.add_regressor(col)

        model.fit(df)

        # Evaluate using cross-validation
        from prophet.diagnostics import cross_validation, performance_metrics
        cv_results = cross_validation(model, horizon='30 days', period='7 days')
        metrics_df = performance_metrics(cv_results)

        metrics = {
            'mape': float(metrics_df['mape'].mean()) if 'mape' in metrics_df else None,
            'rmse': float(metrics_df['rmse'].mean()),
            'mae': float(metrics_df['mae'].mean()),
        }

        return TrainingResult(
            model=model,
            preprocessor=None,
            metrics=metrics,
            feature_importance=[],
            training_log=f"Trained Prophet on {len(df)} rows",
            feature_columns=['ds'],
            target_column='y'
        )
```

#### Anomaly Detection Engine

```python
# backend/app/modules/ml/engine/anomaly.py

class AnomalyDetectionEngine(BaseMLEngine):
    def train(self, df, target, features, hyperparams) -> TrainingResult:
        from sklearn.ensemble import IsolationForest

        contamination = hyperparams.get('contamination', 0.05)
        n_estimators = hyperparams.get('n_estimators', 100)

        preprocessor = self._build_preprocessor(df, features)
        X = preprocessor.fit_transform(df[features])

        model = IsolationForest(
            contamination=contamination,
            n_estimators=n_estimators,
            random_state=42
        )
        model.fit(X)

        # Score training data
        scores = model.decision_function(X)
        labels = model.predict(X)
        n_anomalies = (labels == -1).sum()

        metrics = {
            'anomaly_ratio': float(n_anomalies / len(df)),
            'mean_anomaly_score': float(scores[labels == -1].mean()) if n_anomalies > 0 else 0,
            'mean_normal_score': float(scores[labels == 1].mean()),
        }

        return TrainingResult(
            model=model,
            preprocessor=preprocessor,
            metrics=metrics,
            feature_importance=[{"feature": f, "importance": 1.0 / len(features)} for f in features],
            training_log=f"Trained IsolationForest on {len(df)} rows, found {n_anomalies} anomalies",
            feature_columns=features,
            target_column=target
        )
```

### 7.4 ML Service (Orchestrator)

```python
# backend/app/modules/ml/service.py

class MLService:
    def __init__(self):
        self.engines = {
            'classification': ClassificationEngine(),
            'regression': RegressionEngine(),
            'forecast': ForecastEngine(),
            'anomaly': AnomalyDetectionEngine(),
        }
        self.serializer = ModelSerializer()    # Pickle → MinIO
        self.udf_manager = UDFManager()         # Create StarRocks Python UDFs
        self.repo = MLRepository()

    async def train_model(
        self,
        model_name: str,
        model_type: str,
        algorithm: str,
        training_query: str,
        target_column: str,
        timestamp_column: str | None,
        series_column: str | None,
        hyperparams: dict,
        database: str,
        owner: str,
    ) -> dict:
        """Train a new ML model or create a new version."""

        # 1. Check if model exists → new version if so
        existing = await self.repo.get_model_by_name(model_name, database)
        if existing:
            version = existing['current_version'] + 1
            model_id = existing['model_id']
        else:
            version = 1
            model_id = str(uuid.uuid4())

        # 2. Create training run record
        run_id = str(uuid.uuid4())
        await self.repo.create_training_run(run_id, model_id, version, 'running', owner)

        try:
            # 3. Execute training query → DataFrame
            df = await self._execute_training_query(training_query, database)

            # 4. Determine feature columns (all except target + timestamp)
            exclude = {target_column, timestamp_column, series_column} - {None}
            features = [c for c in df.columns if c not in exclude]

            # 5. Train
            engine = self.engines[model_type]
            result = engine.train(
                df=df,
                target=target_column,
                features=features,
                hyperparams={**hyperparams, 'algorithm': algorithm,
                             'timestamp_column': timestamp_column,
                             'series_column': series_column}
            )

            # 6. Serialize to MinIO
            artifact_path = f"nova-ml-artifacts/{model_name}/v{version}/model.pkl"
            preprocessor_path = f"nova-ml-artifacts/{model_name}/v{version}/preprocessor.pkl"
            await self.serializer.save(result.model, artifact_path)
            if result.preprocessor:
                await self.serializer.save(result.preprocessor, preprocessor_path)

            # 7. Register in ML_MODELS
            if existing:
                await self.repo.update_model(model_id, version=version,
                    status='ready', metrics=result.metrics,
                    artifact_path=artifact_path,
                    feature_importance=result.feature_importance,
                    hyperparams=hyperparams)
                await self.repo.create_version(model_id, version, ...)
            else:
                await self.repo.create_model(model_id, model_name, database,
                    model_type=model_type, algorithm=algorithm,
                    status='ready', features=features,
                    target=target_column, metrics=result.metrics,
                    artifact_path=artifact_path,
                    preprocessor_path=preprocessor_path,
                    feature_importance=result.feature_importance,
                    hyperparams=hyperparams, owner=owner,
                    training_query=training_query,
                    training_row_count=len(df))
                await self.repo.create_version(model_id, version, ...)

            # 8. Create/update Python UDF for inference
            await self.udf_manager.register_predict_udf(model_name, model_id)

            # 9. Complete training run
            await self.repo.complete_training_run(run_id, 'completed',
                metrics=result.metrics, training_row_count=len(df))

            return {
                "model_id": model_id,
                "model_name": model_name,
                "version": version,
                "status": "ready",
                "metrics": result.metrics,
                "feature_importance": result.feature_importance,
                "training_rows": len(df),
            }

        except Exception as e:
            await self.repo.complete_training_run(run_id, 'failed',
                error_message=str(e))
            if not existing:
                await self.repo.create_model(model_id, model_name, database,
                    model_type=model_type, algorithm=algorithm,
                    status='failed', owner=owner)
            raise NovaException(f"Training failed: {e}", status_code=422)
```

### 7.5 Python UDF for Inference

```python
# backend/app/modules/ml/udf_manager.py

class UDFManager:
    async def register_predict_udf(self, model_name: str, model_id: str):
        """Create a StarRocks Python UDF for model inference."""

        # Package the inference code as a .py.zip
        # Upload to MinIO
        zip_url = await self._create_inference_package(model_name, model_id)

        # Register UDF
        sql = f"""
        CREATE OR REPLACE FUNCTION ML_PREDICT_{model_name}(features VARCHAR)
        RETURNS VARCHAR
        TYPE = 'Python'
        SYMBOL = 'nova_ml_inference.predict'
        FILE = '{zip_url}'
        AS $$
        import pickle, json, numpy as np
        from sklearn.pipeline import Pipeline

        _model = None
        _preprocessor = None

        def _load():
            global _model, _preprocessor
            if _model is None:
                _model = pickle.loads(open('/tmp/model.pkl', 'rb').read())
                _preprocessor = pickle.loads(open('/tmp/preprocessor.pkl', 'rb').read())
            return _model, _preprocessor

        def predict(features_json: str) -> str:
            model, preprocessor = _load()
            features = json.loads(features_json)
            X = preprocessor.transform([features])
            pred = model.predict(X)
            proba = model.predict_proba(X) if hasattr(model, 'predict_proba') else None
            result = {{
                "prediction": str(pred[0]),
                "probability": proba[0].tolist() if proba is not None else None
            }}
            return json.dumps(result)
        $$
        """
        await db.execute_system(sql)
```

### 7.6 Alternative Inference (Without Python UDF on BE)

If Python UDFs are not available on BE nodes, use **backend-mediated inference**:

```sql
-- User writes:
SELECT ML_PREDICT('churn_v1', age, income, tenure) FROM customers;

-- Nova SQL parser rewrites to:
-- 1. Backend fetches model + preprocessor from MinIO
-- 2. Backend runs prediction in-process
-- 3. Results injected back into query response
```

This is slower but works without modifying BE nodes.

### 7.7 API Endpoints

```
POST   /api/v1/ml/train                           — Train a model
GET    /api/v1/ml/models                           — List all models
GET    /api/v1/ml/models/{model_id}                — Model detail
GET    /api/v1/ml/models/{model_id}/versions       — Version history
PUT    /api/v1/ml/models/{model_id}/default-version — Set default version
POST   /api/v1/ml/models/{model_id}/predict        — Test prediction
POST   /api/v1/ml/models/{model_id}/evaluate       — Evaluate model
GET    /api/v1/ml/models/{model_id}/feature-importance — Feature weights
DELETE /api/v1/ml/models/{model_id}                — Drop model
POST   /api/v1/ml/models/{model_id}/retrain        — Retrain with new data
GET    /api/v1/ml/training-runs                    — All training runs
GET    /api/v1/ml/training-runs/{run_id}           — Run detail + logs
```

---

## 8. Phase 3: Database Explorer Integration

> **Effort**: 3-4 days | **Impact**: 🔥 High | **Dependencies**: Phase 2 tables + backend

### 8.1 Tree Structure

Add "ML Models" as the 7th group in every database:

```
└── NOVA_DEMO
    ├── Tables (5)
    ├── Views (2)
    ├── Materialized Views (1)
    ├── Functions (2)
    ├── Pipes (0)
    ├── Stages (1)
    └── ML Models (2)               ◄── NEW
        ├── 🤖 churn_predictor       ◄── Classification · Ready · Acc: 0.92
        │   ├── v3 (default)         ◄── Current version
        │   ├── v2
        │   └── v1
        └── 📈 revenue_forecast      ◄── Forecast · Ready · MAPE: 3.2%
            └── v1 (default)
```

### 8.2 Backend Explorer Endpoints

```python
# Add to explorer/repository.py

async def list_ml_models(self, database: str) -> list[dict]:
    """List ML models for a database."""
    query = """
    SELECT model_id, model_name, model_type, algorithm, status,
           current_version, default_version,
           JSON_PARSE(metrics) AS metrics,
           JSON_PARSE(feature_importance) AS feature_importance,
           training_row_count, owner, created_at, updated_at
    FROM NOVA_SYSTEM.ML_MODELS
    WHERE database_name = %s AND status != 'archived'
    ORDER BY updated_at DESC
    """
    return await db.execute_system(query, [database])

async def get_ml_model_detail(self, model_id: str) -> dict:
    """Get detailed model info."""
    query = """
    SELECT m.*, v.version, v.metrics AS version_metrics,
           v.training_log, v.created_at AS version_created_at
    FROM NOVA_SYSTEM.ML_MODELS m
    LEFT JOIN NOVA_SYSTEM.ML_MODEL_VERSIONS v
        ON v.model_id = m.model_id AND v.version = m.default_version
    WHERE m.model_id = %s
    """
    return await db.execute_system(query, [model_id], fetch_one=True)

async def get_model_versions(self, model_id: str) -> list[dict]:
    """List all versions of a model."""
    query = """
    SELECT version, status, metrics, hyperparams,
           training_row_count, training_log, comment,
           created_at, created_by
    FROM NOVA_SYSTEM.ML_MODEL_VERSIONS
    WHERE model_id = %s
    ORDER BY version DESC
    """
    return await db.execute_system(query, [model_id])
```

### 8.3 Detail Panel Design

```
┌─────────────────────────────────────────────────────────┐
│ 🤖 churn_predictor                                      │
│ Classification · XGBoost · Version 3 (default)          │
│ Status: ● Ready                                         │
│ Database: NOVA_DEMO · Owner: nova_admin                 │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ ┌── Metrics ──────────────────────────────────────────┐ │
│ │                                                     │ │
│ │  Accuracy   Precision   Recall   F1       AUC      │ │
│ │  ┌──────┐   ┌──────┐   ┌────┐  ┌────┐  ┌──────┐  │ │
│ │  │ 92.3%│   │ 90.5%│   │87.8%│ │89.1%│ │ 95.6%│  │ │
│ │  └──────┘   └──────┘   └────┘  └────┘  └──────┘  │ │
│ └─────────────────────────────────────────────────────┘ │
│                                                         │
│ ┌── Feature Importance ───────────────────────────────┐ │
│ │                                                     │ │
│ │  ████████████████████  tenure        0.35           │ │
│ │  ██████████████        income        0.24           │ │
│ │  ██████████            age           0.18           │ │
│ │  ████████              plan_type     0.13           │ │
│ │  ██████                support_calls 0.10           │ │
│ └─────────────────────────────────────────────────────┘ │
│                                                         │
│ ┌── Versions ─────────────────────────────────────────┐ │
│ │                                                     │ │
│ │  Version  Status  Accuracy  Rows     Date    By     │ │
│ │  ───────  ──────  ────────  ───────  ──────  ───   │ │
│ │  v3 ●     Ready   92.3%     15,420   Jun 20  admin │ │
│ │  v2       Ready   89.1%     12,300   Jun 15  admin │ │
│ │  v1       Ready   84.7%      8,500   Jun 10  admin │ │
│ └─────────────────────────────────────────────────────┘ │
│                                                         │
│ ┌── Properties ───────────────────────────────────────┐ │
│ │  Target:        churned                             │ │
│ │  Features:      age, income, tenure, plan_type,     │ │
│ │                 support_calls                       │ │
│ │  Algorithm:     xgboost                             │ │
│ │  Hyperparams:   n_estimators=200, max_depth=5       │ │
│ │  Training Query: SELECT age, income, ... FROM ...   │ │
│ └─────────────────────────────────────────────────────┘ │
│                                                         │
│ ┌── Actions ──────────────────────────────────────────┐ │
│ │  [▶ Test Prediction]  [🔄 Retrain]  [📊 Evaluate]   │ │
│ │  [⬆ Set as Default]   [🗑 Drop Model]              │ │
│ └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### 8.4 Test Prediction Dialog

```
┌──────────────────────────────────────────┐
│ ▶ Test Prediction: churn_predictor       │
│                                          │
│ Enter feature values:                    │
│ ┌──────────────────────────────────────┐ │
│ │ Feature        │ Value               │ │
│ │────────────────│─────────────────────│ │
│ │ age            │ [35          ]      │ │
│ │ income         │ [75000       ]      │ │
│ │ tenure         │ [24          ]      │ │
│ │ plan_type      │ [premium     ▼]     │ │
│ │ support_calls  │ [3           ]      │ │
│ └──────────────────────────────────────┘ │
│                                          │
│ Or: Paste SQL query                      │
│ ┌──────────────────────────────────────┐ │
│ │ SELECT 35 AS age, 75000 AS income...│ │
│ └──────────────────────────────────────┘ │
│                                          │
│ [Predict]                                │
│                                          │
│ Result:                                  │
│ ┌──────────────────────────────────────┐ │
│ │ Prediction: WILL CHURN (Yes)         │ │
│ │ Confidence: 87.3%                    │ │
│ │ Probabilities:                       │ │
│ │   Churn:    87.3% ████████░░         │ │
│ │   No Churn: 12.7% █░░░░░░░░░         │ │
│ └──────────────────────────────────────┘ │
└──────────────────────────────────────────┘
```

---

## 9. Phase 4: Training Wizard UI

> **Effort**: 1 week | **Impact**: 🟡 Medium | **Dependencies**: Phase 2 backend

### 9.1 Wizard Flow

```
Step 1: Model Type
  ┌─────────────────────────────────────────────┐
  │ What do you want to predict?                │
  │                                             │
  │ ┌─────────┐ ┌──────────┐ ┌───────────────┐ │
  │ │ 🏷️      │ │ 📈       │ │ 🔍            │ │
  │ │ Classify│ │ Forecast │ │ Anomaly       │ │
  │ │         │ │          │ │ Detection     │ │
  │ │ Predict │ │ Predict  │ │               │ │
  │ │ a       │ │ future   │ │ Find unusual  │ │
  │ │ category│ │ values   │ │ patterns      │ │
  │ └─────────┘ └──────────┘ └───────────────┘ │
  │                                             │
  │ ┌──────────┐                                │
  │ │ 📊       │                                │
  │ │ Regress  │                                │
  │ │          │                                │
  │ │ Predict  │                                │
  │ │ a number │                                │
  │ └──────────┘                                │
  └─────────────────────────────────────────────┘

Step 2: Training Data
  ┌─────────────────────────────────────────────┐
  │ Where is your training data?                │
  │                                             │
  │ Database: [NOVA_DEMO            ▼]          │
  │                                             │
  │ ○ Select table/view                         │
  │   Table: [customer_features     ▼]          │
  │                                             │
  │ ○ Write SQL query                           │
  │   ┌─────────────────────────────────────┐   │
  │   │ SELECT age, income, tenure,         │   │
  │   │        plan_type, support_calls,    │   │
  │   │        churned                      │   │
  │   │ FROM customer_features              │   │
  │   │ WHERE created_at > '2025-01-01'     │   │
  │   └─────────────────────────────────────┘   │
  │                                             │
  │ [Preview Data]  → Shows first 5 rows        │
  │                                             │
  │ Rows: 15,420 · Columns: 6                   │
  └─────────────────────────────────────────────┘

Step 3: Configure
  ┌─────────────────────────────────────────────┐
  │ Configure your model                        │
  │                                             │
  │ Model Name: [churn_predictor     ]          │
  │                                             │
  │ Target Column: [churned          ▼]         │
  │                                             │
  │ Feature Columns:                            │
  │ ☑ age (INT)                                 │
  │ ☑ income (DECIMAL)                          │
  │ ☑ tenure (INT)                              │
  │ ☑ plan_type (VARCHAR)                       │
  │ ☑ support_calls (INT)                       │
  │ ☐ created_at (DATETIME)                     │
  │                                             │
  │ Algorithm: [XGBoost              ▼]         │
  │                                             │
  │ ▸ Advanced Hyperparameters                  │
  │   n_estimators: [200]                       │
  │   max_depth:    [5   ]                       │
  │   learning_rate:[0.1 ]                       │
  └─────────────────────────────────────────────┘

Step 4: Review & Train
  ┌─────────────────────────────────────────────┐
  │ Review and start training                   │
  │                                             │
  │ ┌─────────────────────────────────────────┐ │
  │ │ Model:     churn_predictor              │ │
  │ │ Type:      Classification               │ │
  │ │ Algorithm: XGBoost                      │ │
  │ │ Target:    churned                      │ │
  │ │ Features:  age, income, tenure,         │ │
  │ │            plan_type, support_calls     │ │
  │ │ Rows:      15,420                       │ │
  │ │ Test Split: 20%                         │ │
  │ └─────────────────────────────────────────┘ │
  │                                             │
  │ [🚀 Start Training]                         │
  │                                             │
  │ ┌─ Training Progress ────────────────────┐  │
  │ │ ● Loading data...                      │  │
  │ │ ● Preprocessing features...            │  │
  │ │ ● Training XGBoost (200 estimators)... │  │
  │ │ ● Evaluating on test set...            │  │
  │ │ ✅ Complete!                           │  │
  │ │                                        │  │
  │ │ Accuracy: 92.3%  F1: 89.1%  AUC: 95.6%│  │
  │ └────────────────────────────────────────┘  │
  └─────────────────────────────────────────────┘
```

### 9.2 SQL Generation

The wizard generates the equivalent SQL:

```sql
-- Generated by ML Training Wizard
CREATE ML_MODEL churn_predictor
TYPE = CLASSIFICATION
ALGORITHM = XGBOOST
HYPERPARAMS = {"n_estimators": 200, "max_depth": 5, "learning_rate": 0.1}
AS SELECT age, income, tenure, plan_type, support_calls
   FROM NOVA_DEMO.customer_features
TARGET churned;
```

User can copy this SQL and run it in the worksheet for future retraining.

---

## 10. Phase 5: Model Monitoring & Observability

> **Effort**: 1 week | **Impact**: 🟢 Low (later) | **Dependencies**: Phase 2 + audit trail

### 10.1 Prediction Logging

Every `ML_PREDICT` call logs to `ML_PREDICTION_LOGS`:

```python
# In the Python UDF or backend inference handler
async def log_prediction(model_id: str, version: int, count: int,
                         latency_ms: float, user: str):
    await repo.insert_prediction_log(
        log_id=str(uuid.uuid4()),
        model_id=model_id,
        model_version=version,
        prediction_count=count,
        avg_latency_ms=latency_ms,
        called_by=user
    )
```

### 10.2 Drift Detection

```sql
-- Compare feature distributions: training vs production
-- (Backend computes statistics periodically)
SELECT
    feature_name,
    training_mean,
    production_mean,
    ABS(production_mean - training_mean) / (training_stddev || 1) AS z_score,
    CASE WHEN ABS(production_mean - training_mean) / (training_stddev || 1) > 2
         THEN 'DRIFT DETECTED' ELSE 'OK' END AS status
FROM NOVA_SYSTEM.ML_FEATURE_STATS
WHERE model_id = 'churn_predictor'
ORDER BY z_score DESC;
```

### 10.3 Monitoring Dashboard

```
┌─────────────────────────────────────────────────────────┐
│ Model Monitoring: churn_predictor                        │
│                                                         │
│ ┌── Usage ─────────────────────────────────────────────┐│
│ │ Predictions (7d): 12,450                             ││
│ │ Avg Latency:     45ms                                ││
│ │ Active Users:    5                                   ││
│ └──────────────────────────────────────────────────────┘│
│                                                         │
│ ┌── Prediction Distribution (7d) ─────────────────────┐│
│ │                                                      ││
│ │     ██████                                           ││
│ │     ██████ ██                                        ││
│ │     ██████ ██ ██                                     ││
│ │ Churn: ██████ ██ ██ ██    No Churn: ██ ██ ██ ██ ██ ██│
│ │ 34.2%                              65.8%              ││
│ └──────────────────────────────────────────────────────┘│
│                                                         │
│ ┌── Feature Drift ─────────────────────────────────────┐│
│ │ Feature        Train Mean  Prod Mean  Z-Score  Status││
│ │ ─────────────  ──────────  ─────────  ───────  ──────││
│ │ age            38.2        39.1       0.45     OK    ││
│ │ income         72,500      71,800     0.32     OK    ││
│ │ tenure         24.5        18.2       3.12     ⚠️ DRIFT││
│ │ support_calls  2.3         2.1        0.18     OK    ││
│ └──────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
```

---

## 11. SQL Dialect Reference

### 11.1 DDL Commands

```sql
-- ============================================================
-- CREATE ML_MODEL: Train a new model
-- ============================================================
CREATE ML_MODEL model_name
TYPE = { CLASSIFICATION | REGRESSION | FORECAST | ANOMALY_DETECTION }
[ ALGORITHM = { XGBOOST | GRADIENT_BOOSTING | RANDOM_FOREST | LOGISTIC_REGRESSION
              | LINEAR_REGRESSION | PROPHET | ISOLATION_FOREST } ]
[ HYPERPARAMS = {json_object} ]
AS select_query
TARGET column_name
[ TIMESTAMP timestamp_column ]       -- Required for FORECAST
[ SERIES series_column ];             -- Optional for multi-series FORECAST

-- ============================================================
-- ALTER ML_MODEL: Version management
-- ============================================================
ALTER ML_MODEL model_name SET DEFAULT_VERSION = version_number;
ALTER ML_MODEL model_name ARCHIVE VERSION version_number;
ALTER ML_MODEL model_name SET COMMENT = 'comment text';
ALTER ML_MODEL model_name RENAME TO new_name;

-- ============================================================
-- DROP ML_MODEL: Delete model + artifacts
-- ============================================================
DROP ML_MODEL model_name;
DROP ML_MODEL IF EXISTS model_name;

-- ============================================================
-- SHOW ML_MODELS: List models
-- ============================================================
SHOW ML_MODELS;
SHOW ML_MODELS FROM database_name;
SHOW ML_MODELS LIKE 'pattern';
SHOW ML_MODEL model_name;                    -- Detail of one model
SHOW ML_MODEL model_name VERSIONS;           -- Version history

-- ============================================================
-- DESCRIBE: Model structure
-- ============================================================
DESCRIBE ML_MODEL model_name;                -- Features, target, algorithm
```

### 11.2 DML Functions (Inference)

```sql
-- ============================================================
-- ML_PREDICT: Run inference
-- ============================================================
ML_PREDICT('model_name', feature1, feature2, ...)
-- Returns: predicted value (VARCHAR)

ML_PREDICT_PROBA('model_name', feature1, feature2, ...)
-- Returns: probability/confidence (FLOAT)

-- ============================================================
-- ML_FORECAST: Time-series prediction
-- ============================================================
ML_FORECAST('model_name', PERIODS => n)
-- Returns: table with (timestamp, predicted_value, lower_bound, upper_bound)

-- ============================================================
-- ML_EVALUATE: Model metrics
-- ============================================================
ML_EVALUATE('model_name')
-- Returns: accuracy, precision, recall, f1, auc (classification)
--          rmse, mae, mape, r2 (regression/forecast)
--          anomaly_ratio, mean_score (anomaly)

-- ============================================================
-- ML_FEATURE_IMPORTANCE: Feature weights
-- ============================================================
ML_FEATURE_IMPORTANCE('model_name')
-- Returns: table with (feature_name, importance_score)

-- ============================================================
-- ML_CONFUSION_MATRIX: Classification confusion matrix
-- ============================================================
ML_CONFUSION_MATRIX('model_name')
-- Returns: table with (actual, predicted, count)

-- ============================================================
-- ML_DETECT_ANOMALIES: Anomaly detection
-- ============================================================
ML_DETECT_ANOMALIES('model_name', feature1, feature2, ...)
-- Returns: -1 (anomaly) or 1 (normal) + anomaly_score
```

### 11.3 LLM Functions

```sql
-- ============================================================
-- AI_COMPLETE: General LLM completion
-- ============================================================
AI_COMPLETE(prompt VARCHAR) → VARCHAR

-- ============================================================
-- AI_SENTIMENT: Sentiment analysis
-- ============================================================
AI_SENTIMENT(text VARCHAR) → VARCHAR  -- JSON: {"sentiment": "...", "confidence": 0.0}

-- ============================================================
-- AI_CLASSIFY: Zero-shot classification
-- ============================================================
AI_CLASSIFY(text VARCHAR, categories VARCHAR) → VARCHAR
-- categories: comma-separated list

-- ============================================================
-- AI_SUMMARIZE: Text summarization
-- ============================================================
AI_SUMMARIZE(text VARCHAR) → VARCHAR

-- ============================================================
-- AI_EXTRACT: Structured entity extraction
-- ============================================================
AI_EXTRACT(text VARCHAR, schema VARCHAR) → VARCHAR  -- JSON with extracted fields
-- schema: JSON object describing fields to extract

-- ============================================================
-- AI_TRANSLATE: Translation
-- ============================================================
AI_TRANSLATE(text VARCHAR, target_language VARCHAR) → VARCHAR

-- ============================================================
-- AI_FILTER: Semantic boolean filter
-- ============================================================
AI_FILTER(text VARCHAR, criteria VARCHAR) → VARCHAR  -- 'true' or 'false'
```

---

## 12. API Endpoints Reference

### 12.1 LLM Function Management

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/ai/aliases` | List all model aliases |
| `POST` | `/api/v1/ai/aliases` | Create alias |
| `PUT` | `/api/v1/ai/aliases/{id}` | Update alias |
| `DELETE` | `/api/v1/ai/aliases/{id}` | Delete alias |
| `POST` | `/api/v1/ai/register-udfs` | Re-register all LLM UDFs |
| `GET` | `/api/v1/ai/udf-status` | Check registered UDFs |

### 12.2 ML Model Management

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/ml/train` | Train new model / new version |
| `GET` | `/api/v1/ml/models` | List all models |
| `GET` | `/api/v1/ml/models/{id}` | Model detail + metrics |
| `DELETE` | `/api/v1/ml/models/{id}` | Drop model + artifacts |
| `PUT` | `/api/v1/ml/models/{id}/default-version` | Set default version |
| `POST` | `/api/v1/ml/models/{id}/retrain` | Retrain with new data |
| `PUT` | `/api/v1/ml/models/{id}/archive` | Archive model |

### 12.3 ML Evaluation & Prediction

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/ml/models/{id}/predict` | Test prediction with sample data |
| `POST` | `/api/v1/ml/models/{id}/evaluate` | Evaluate model quality |
| `GET` | `/api/v1/ml/models/{id}/feature-importance` | Feature importance scores |
| `GET` | `/api/v1/ml/models/{id}/confusion-matrix` | Confusion matrix |

### 12.4 ML Versions & History

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/ml/models/{id}/versions` | Version history |
| `GET` | `/api/v1/ml/training-runs` | All training runs (paginated) |
| `GET` | `/api/v1/ml/training-runs/{id}` | Run detail + logs |
| `GET` | `/api/v1/ml/prediction-logs` | Prediction usage logs |

### 12.5 Explorer ML Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/explorer/databases/{db}/ml-models` | ML models in database |
| `GET` | `/api/v1/explorer/databases/{db}/ml-models/{name}` | Model detail |
| `GET` | `/api/v1/explorer/databases/{db}/ml-models/{name}/versions` | Versions |

---

## 13. Database Schema Reference

### 13.1 New Tables

| Table | Key Type | Purpose |
|-------|----------|---------|
| `CONFIG_MODEL_ALIASES` | PRIMARY KEY | LLM function → provider/model mapping |
| `ML_MODELS` | PRIMARY KEY | Model registry |
| `ML_MODEL_VERSIONS` | PRIMARY KEY | Version history |
| `ML_TRAINING_RUNS` | PRIMARY KEY | Training audit trail |
| `ML_PREDICTION_LOGS` | DUPLICATE KEY | Prediction usage logs |
| `ML_FEATURE_STATS` | PRIMARY KEY | Feature drift statistics |

### 13.2 Existing Tables (No Change)

| Table | Purpose |
|-------|---------|
| `CONFIG_AI_PROVIDERS` | LLM provider config (name, type, endpoint, api_key) |
| `CONFIG_AI_MODELS` | LLM model config (name, type, max_tokens) |

### 13.3 MinIO Bucket Structure

```
nova-ml-artifacts/
├── {model_name}/
│   ├── v1/
│   │   ├── model.pkl            # Serialized sklearn/prophet model
│   │   ├── preprocessor.pkl     # Fitted preprocessor pipeline
│   │   └── metadata.json        # Training config snapshot
│   ├── v2/
│   │   ├── model.pkl
│   │   ├── preprocessor.pkl
│   │   └── metadata.json
│   └── udf/
│       └── inference.py.zip     # Python UDF package for StarRocks
```

---

## 14. Implementation Timeline

```
Week 1 ─────────────────────────────────────────────────
  Day 1-2:  Phase 1 — LLM Alias table + service + API
  Day 2-3:  Phase 1 — SQL UDF registration + templates
  Day 3:    Phase 1 — Frontend: Functions tab on AI Providers page
  Day 4-5:  Phase 2 — Fix ML schema (migration) + ML Engine base

Week 2 ─────────────────────────────────────────────────
  Day 1-2:  Phase 2 — ClassificationEngine (XGBoost/GBM)
  Day 3-4:  Phase 2 — ForecastEngine (Prophet)
  Day 5:    Phase 2 — AnomalyDetectionEngine (IsolationForest)

Week 3 ─────────────────────────────────────────────────
  Day 1-2:  Phase 2 — ML Service (orchestrator) + API endpoints
  Day 3-4:  Phase 2 — Model serializer (MinIO) + UDF manager
  Day 5:    Phase 2 — SQL dialect parser (CREATE ML_MODEL, SHOW ML_MODELS)

Week 4 ─────────────────────────────────────────────────
  Day 1-2:  Phase 3 — Explorer backend (ML model tree nodes)
  Day 3-4:  Phase 3 — Explorer frontend (ML Models group + detail panel)
  Day 5:    Phase 3 — Test Prediction dialog

Week 5 ─────────────────────────────────────────────────
  Day 1-3:  Phase 4 — Training Wizard UI (4-step flow)
  Day 4-5:  Phase 4 — Polish, dark mode, integration test

Week 6 (Optional) ──────────────────────────────────────
  Day 1-3:  Phase 5 — Model monitoring dashboard
  Day 4-5:  Phase 5 — Drift detection + prediction logging
```

---

## 15. Key Design Decisions

### 15.1 Decision Matrix

| Decision | Choice | Rationale | Alternative Considered |
|----------|--------|-----------|----------------------|
| Model storage | MinIO pickle + StarRocks metadata | Same as Stages pattern; metadata queryable | Store model BLOB in StarRocks (too large) |
| ML inference | Python UDF for ML, SQL UDF for LLM | Python UDF handles complex sklearn models | Backend-only inference (no scale) |
| Model addressing | `ML_PREDICT('model_name', col1, ...)` | Simple string-based, works with autocomplete | Namespace: `db.schema.model` (StarRocks has no nested schemas) |
| Training trigger | `CREATE ML_MODEL ... AS SELECT` | Matches BigQuery/Snowflake pattern | `CALL train_model(...)` (less native feel) |
| Versioning | Auto-increment on retrain + `default_version` | Simple, matches Snowflake | Git-like branching (over-engineered) |
| LLM provider config | Reuse `CONFIG_AI_PROVIDERS` | Already built | Separate LLM config table (duplication) |
| Model per-database | `database_name` column on ML_MODELS | Models belong to a database; shows in Explorer tree | Global models (no database association) |
| Preprocessing | Sklearn Pipeline serialized alongside model | Reproducible; same pipeline for train + predict | Manual feature engineering (fragile) |
| Forecasting engine | Prophet (primary) + GBM (fallback) | Prophet handles seasonality well; GBM for large datasets | ARIMA only (less flexible) |

### 15.2 Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Python UDF not available on BE | Fallback: backend-mediated inference (slower but works) |
| Large training datasets OOM | Stream data in batches; limit training to 100K rows initially |
| Model pickle compatibility | Pin sklearn/prophet versions; include version in metadata |
| StarRocks SQL UDF limitations | Use CONCAT-based approach for dynamic config; re-register on change |
| Concurrent training requests | Queue-based with max 3 concurrent; return run_id for polling |

---

## 16. Appendix: Platform Comparison Matrix

| Feature | Snowflake | BigQuery ML | Redshift ML | Databricks | DuckDB | ClickHouse | **Nova (Target)** |
|---------|-----------|-------------|-------------|------------|--------|------------|-------------------|
| **CREATE MODEL DDL** | ✅ | ✅ | ✅ | ❌ (Python) | ❌ (ext) | ❌ (agg) | ✅ |
| **Model types** | 3 (class/fore/anom) | 22+ | 4 (auto/xgb/km/forecast) | N/A | 4 (via mlpack) | 3 (lin/log/bayes) | 4 (class/reg/fore/anom) |
| **ML.PREDICT in SQL** | ✅ `model!PREDICT` | ✅ `ML.PREDICT()` | ✅ `fn(args)` | ✅ `ai_query()` | ✅ `infera_predict` | ✅ `evalMLMethod` | ✅ `ML_PREDICT()` |
| **ML.EVALUATE** | ✅ `!SHOW_EVAL` | ✅ `ML.EVALUATE()` | ❌ (SHOW MODEL) | ❌ (MLflow UI) | ❌ | ❌ | ✅ `ML_EVALUATE()` |
| **Explainability** | ✅ `!EXPLAIN_FI` | ✅ `ML.EXPLAIN_PREDICT` | ❌ | ❌ | ❌ | ❌ | ✅ `ML_FEATURE_IMPORTANCE()` |
| **Hyperparameter tuning** | ❌ (config only) | ✅ `HPARAM_RANGE` | ✅ `HYPERPARAMETERS` | ❌ | ❌ | ❌ | ⚠️ Manual config |
| **Model versioning** | ✅ `ALTER MODEL` | ✅ Vertex AI | ❌ | ✅ MLflow | ❌ | ❌ | ✅ `ML_MODEL_VERSIONS` |
| **Object browser** | ✅ `SHOW MODELS` | ✅ `INFORMATION_SCHEMA` | ✅ `SHOW MODEL` | ✅ Unity Catalog | ❌ | ❌ | ✅ DB Explorer |
| **LLM functions** | ✅ 12+ built-in | ✅ Remote models | ✅ SageMaker | ✅ `ai_query` + 6 fns | ❌ | ❌ | ✅ UDF wrappers |
| **Fine-tuning** | ✅ `FINETUNE()` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ (future) |
| **Vector search** | ✅ | ✅ | ❌ | ✅ | ❌ | ✅ (ANN) | ✅ (HNSW + cosine) |
| **RBAC on models** | ✅ `GRANT USAGE` | ✅ IAM | ✅ `GRANT EXECUTE` | ✅ UC | ❌ | ❌ | ⚠️ DB-level only |
| **Model monitoring** | ✅ `MODEL MONITOR` | ❌ | ❌ | ❌ (manual) | ❌ | ❌ | ⚠️ Basic drift |
| **In-database training** | ✅ | ✅ | ❌ (SageMaker) | ❌ (Spark) | ❌ (local) | ✅ (aggregate) | ✅ (Nova backend) |

---

> **Next Step**: Review this guide, decide priorities, then start Phase 1 implementation.
