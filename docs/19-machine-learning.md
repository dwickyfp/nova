# Module 19: Machine Learning via SQL

> SQL-native ML: train, evaluate, and predict models directly from SQL — like Snowflake Cortex ML Functions.
> Also: LLM integration via `ai_query()` with convenience wrappers.

---

## Overview

Snowflake punya dua layer ML:

| Layer | Snowflake | StarRocks/Nova |
|-------|-----------|----------------|
| **Classical ML** | `SNOWFLAKE.ML.FORECAST`, `CLASSIFICATION`, `ANOMALY_DETECTION` | Nova ML UDFs (wrapping Python ML) |
| **LLM Functions** | `AI_COMPLETE`, `AI_SUMMARIZE`, `AI_SENTIMENT`, `AI_EMBED` | StarRocks `ai_query()` + Nova convenience wrappers |

Nova akan implement **kedua layer** ini.

---

## Layer 1: LLM Functions (Built on `ai_query`)

StarRocks 4.1 punya `ai_query()` — Nova menambah convenience wrappers.

### Current: `ai_query()` (StarRocks native)

```sql
-- Raw ai_query — verbose, manual
SELECT ai_query(
    CONCAT('Classify sentiment: ', review_text),
    '{"model": "gpt-4o-mini", "api-key": "env.OPENAI_API_KEY"}'
) AS sentiment
FROM reviews;
```

### Nova Enhancement: Model Alias Registry

Register model configs sekali, pakai nama pendek:

```sql
-- Register model alias (via UI or SQL)
CREATE MODEL ALIAS gpt4mini
    ENDPOINT = 'https://api.openai.com/v1'
    MODEL = 'gpt-4o-mini'
    API_KEY = '${OPENAI_API_KEY}';  -- from .env

-- Use with short name
SELECT AI_COMPLETE('gpt4mini', 'Summarize: ' || text) FROM docs;
```

### Nova Convenience Functions

```sql
-- Sentiment analysis (returns: positive/negative/neutral)
SELECT AI_SENTIMENT(review_text) FROM reviews;
-- Equivalent: ai_query(CONCAT('Rate sentiment: ', text), config)

-- Summarization
SELECT AI_SUMMARIZE(article_body, 100) FROM news;  -- 100 words max

-- Translation
SELECT AI_TRANSLATE(text, 'en', 'id') FROM documents;

-- Text classification
SELECT AI_CLASSIFY(ticket_text, ARRAY('billing', 'technical', 'general')) FROM tickets;

-- Entity extraction (returns JSON)
SELECT AI_EXTRACT(order_email, ARRAY('order_id', 'customer_name', 'amount')) FROM emails;

-- Embedding generation
SELECT AI_EMBED('e5-base-v2', text) FROM documents;
-- Returns: vector(768)

-- Boolean filter
SELECT * FROM reviews WHERE AI_FILTER('Is this review about food quality?', review_text);

-- Generic completion (full control)
SELECT AI_COMPLETE('gpt4mini', 'Write a SQL query to: ' || description) FROM requests;
```

### Implementation

```python
# services/ai_functions.py

class AIFunctionService:
    """Nova AI functions — convenience wrappers around ai_query()."""
    
    def __init__(self, model_registry: dict):
        self.models = model_registry  # from nova.yaml
    
    def sentiment(self, text: str, model: str = "default") -> str:
        config = self._get_model_config(model)
        return f"""ai_query(
            CONCAT('Rate sentiment as positive/negative/neutral. Reply with only the word: ', {text}),
            '{config}'
        )"""
    
    def summarize(self, text: str, max_words: int = 100, model: str = "default") -> str:
        config = self._get_model_config(model)
        return f"""ai_query(
            CONCAT('Summarize in {max_words} words or less: ', {text}),
            '{config}'
        )"""
    
    def translate(self, text: str, source: str, target: str, model: str = "default") -> str:
        config = self._get_model_config(model)
        return f"""ai_query(
            CONCAT('Translate from {source} to {target}: ', {text}),
            '{config}'
        )"""
    
    def classify(self, text: str, categories: list[str], model: str = "default") -> str:
        cats = ", ".join(categories)
        config = self._get_model_config(model)
        return f"""ai_query(
            CONCAT('Classify into one of [{cats}]: ', {text}),
            '{config}'
        )"""
    
    def extract(self, text: str, fields: list[str], model: str = "default") -> str:
        field_list = ", ".join(fields)
        config = self._get_model_config(model)
        return f"""ai_query(
            CONCAT('Extract these fields as JSON [{field_list}]: ', {text}),
            '{config}'
        )"""
    
    def embed(self, text: str, model: str = "default") -> str:
        """Note: requires embedding model endpoint."""
        config = self._get_model_config(model)
        return f"""ai_query(
            CONCAT('Generate embedding vector: ', {text}),
            '{config}'
        )"""
    
    def filter(self, condition: str, text: str, model: str = "default") -> str:
        config = self._get_model_config(model)
        return f"""ai_query(
            CONCAT('{condition} Answer only true or false: ', {text}),
            '{config}'
        )"""
    
    def _get_model_config(self, model_name: str) -> str:
        m = self.models.get(model_name, self.models.get("default"))
        return json.dumps({
            "endpoint_url": m.endpoint,
            "model": m.model,
            "api-key": m.api_key,
        })
```

---

## Layer 2: Classical ML via SQL

Snowflake-style ML functions — train, evaluate, predict from SQL.

### 2.1 Forecasting

```sql
-- TRAIN: Time-series forecast
CREATE SNOWFLAKE.ML.FORECAST sales_forecast(
    INPUT_DATA => TABLE(training_view),
    TIMESTAMP_COLNAME => 'date',
    TARGET_COLNAME => 'sales_amount',
    SERIES_COLNAME => 'store_id',           -- optional: multi-series
    CONFIG_OBJECT => {'method': 'best', 'prediction_interval': 0.95}
);

-- PREDICT
SELECT * FROM TABLE(
    sales_forecast!FORECAST(FORECASTING_PERIODS => 30)
);

-- EVALUATE
CALL sales_forecast!SHOW_EVALUATION_METRICS();
```

**Nova adaptation:**

```sql
-- TRAIN
CREATE ML_MODEL sales_forecast
    TYPE = FORECAST
    INPUT = (SELECT date, sales_amount, store_id FROM training_view)
    TIMESTAMP = 'date'
    TARGET = 'sales_amount'
    SERIES = 'store_id'
    CONFIG = ('method' = 'best', 'prediction_interval' = '0.95');

-- PREDICT
SELECT * FROM ML_FORECAST('sales_forecast', FORECASTING_PERIODS => 30);

-- EVALUATE
SELECT * FROM ML_EVALUATE('sales_forecast');
```

### 2.2 Classification

```sql
-- TRAIN
CREATE SNOWFLAKE.ML.CLASSIFICATION churn_model(
    INPUT_DATA => TABLE(customer_features),
    TARGET_COLNAME => 'churned',
    CONFIG_OBJECT => {'evaluate': TRUE, 'evaluation_config': {'test_fraction': 0.2}}
);

-- PREDICT
SELECT customer_id, churn_model!PREDICT(INPUT_DATA => {*}) AS prediction
FROM active_customers;

-- EVALUATE
CALL churn_model!SHOW_EVALUATION_METRICS();
CALL churn_model!SHOW_CONFUSION_MATRIX();
```

**Nova adaptation:**

```sql
-- TRAIN
CREATE ML_MODEL churn_model
    TYPE = CLASSIFICATION
    INPUT = (SELECT * FROM customer_features)
    TARGET = 'churned'
    CONFIG = ('evaluate' = 'true');

-- PREDICT
SELECT customer_id, ML_PREDICT('churn_model', *) AS prediction
FROM active_customers;

-- EVALUATE
SELECT * FROM ML_EVALUATE('churn_model');
SELECT * FROM ML_CONFUSION_MATRIX('churn_model');
```

### 2.3 Anomaly Detection

```sql
-- TRAIN (supervised or unsupervised)
CREATE SNOWFLAKE.ML.ANOMALY_DETECTION metric_detector(
    INPUT_DATA => TABLE(metrics_history),
    TIMESTAMP_COLNAME => 'timestamp',
    TARGET_COLNAME => 'cpu_usage',
    LABEL_COLNAME => 'is_anomaly',          -- '' for unsupervised
    CONFIG_OBJECT => {'prediction_interval': 0.99}
);

-- DETECT
SELECT * FROM TABLE(
    metric_detector!DETECT_ANOMALIES(
        INPUT_DATA => TABLE(recent_metrics),
        TIMESTAMP_COLNAME => 'timestamp',
        TARGET_COLNAME => 'cpu_usage'
    )
);
```

**Nova adaptation:**

```sql
-- TRAIN
CREATE ML_MODEL metric_detector
    TYPE = ANOMALY_DETECTION
    INPUT = (SELECT timestamp, cpu_usage, is_anomaly FROM metrics_history)
    TIMESTAMP = 'timestamp'
    TARGET = 'cpu_usage'
    LABEL = 'is_anomaly';

-- DETECT
SELECT * FROM ML_DETECT_ANOMALIES('metric_detector',
    INPUT => (SELECT timestamp, cpu_usage FROM recent_metrics));
```

---

## Layer 3: Model Registry

Models are first-class objects in Nova.

### Model Lifecycle

```sql
-- CREATE (train)
CREATE ML_MODEL <name> TYPE = <type> INPUT = (<query>) ...;

-- LIST models
SHOW ML_MODELS;
SELECT * FROM NOVA_SYSTEM.ML_MODELS;

-- MODEL details
DESC ML_MODEL <name>;
SELECT * FROM NOVA_SYSTEM.ML_MODEL_VERSIONS WHERE model_name = '<name>';

-- VERSIONING
ALTER ML_MODEL <name> ADD VERSION 'v2' FROM <training_query>;
ALTER ML_MODEL <name> SET DEFAULT_VERSION = 'v2';
ALTER ML_MODEL <name> DROP VERSION 'v1';

-- ALIAS
ALTER ML_MODEL <name> SET ALIAS 'production' = 'v2';

-- PREDICT with version
SELECT ML_PREDICT('<name>', 'v2', *) FROM data;
SELECT ML_PREDICT('<name>', 'production', *) FROM data;

-- EVALUATE
SELECT * FROM ML_EVALUATE('<name>', 'v2');

-- DROP
DROP ML_MODEL <name>;
```

### Model Metadata Table (NOVA_SYSTEM.ML)

```sql
CREATE SCHEMA IF NOT EXISTS NOVA_SYSTEM.ML;

CREATE TABLE NOVA_SYSTEM.ML_MODELS (
    model_id        VARCHAR(64) PRIMARY KEY,
    model_name      VARCHAR(128) NOT NULL,
    model_type      VARCHAR(32),         -- FORECAST, CLASSIFICATION, ANOMALY_DETECTION
    default_version VARCHAR(32),
    created_at      DATETIME,
    created_by      VARCHAR(128),
    description     TEXT
) PRIMARY KEY(model_id) DISTRIBUTED BY HASH(model_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE NOVA_SYSTEM.ML_MODEL_VERSIONS (
    version_id      VARCHAR(64) PRIMARY KEY,
    model_name      VARCHAR(128),
    version_name    VARCHAR(32),
    training_sql    TEXT,
    training_rows   BIGINT,
    training_duration_ms INT,
    metrics         TEXT,                -- JSON: {"accuracy": 0.95, "f1": 0.72}
    artifact_path   VARCHAR(1024),       -- path to serialized model
    created_at      DATETIME,
    created_by      VARCHAR(128)
) PRIMARY KEY(version_id) DISTRIBUTED BY HASH(version_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE NOVA_SYSTEM.ML_MODEL_ALIASES (
    alias_name      VARCHAR(128),
    model_name      VARCHAR(128),
    version_name    VARCHAR(32),
    updated_at      DATETIME,
    PRIMARY KEY (alias_name, model_name)
) DISTRIBUTED BY HASH(alias_name) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
```

---

## Layer 4: AI Provider Management (LLM)

Nova uses a **Provider → Model** hierarchy managed via admin UI.
No Ollama, no hardcoded models — fully configurable.

### Provider Types

| Type | Endpoint | Auth | Example |
|------|----------|------|---------|
| `openai` | `https://api.openai.com/v1` | Bearer token | OpenAI API |
| `anthropic` | `https://api.anthropic.com/v1` | `x-api-key` header | Anthropic API |
| `openai_compatible` | Any URL | Bearer token | vLLM, DeepSeek, Azure, Groq, etc. |

### Data Model (NOVA_SYSTEM.CONFIG)

```sql
-- AI Provider (connection to LLM service)
CREATE TABLE NOVA_SYSTEM.CONFIG_AI_PROVIDERS (
    id              VARCHAR(64) PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,          -- "OpenAI", "Anthropic", "My vLLM"
    type            VARCHAR(32) NOT NULL,           -- openai, anthropic, openai_compatible
    endpoint        VARCHAR(512) NOT NULL,          -- https://api.openai.com/v1
    api_key         VARCHAR(512),                   -- API key stored directly (configurable via UI)
    default_params  TEXT,                            -- JSON: {"temperature": 0.7}
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by      VARCHAR(128)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

-- AI Model (belongs to a provider)
CREATE TABLE NOVA_SYSTEM.CONFIG_AI_MODELS (
    id              VARCHAR(64) PRIMARY KEY,
    provider_id     VARCHAR(64) NOT NULL,           -- FK to AI_PROVIDERS
    name            VARCHAR(128) NOT NULL,          -- "gpt-4o", "claude-sonnet-4"
    display_name    VARCHAR(256),                   -- "GPT-4o (128K context)"
    type            VARCHAR(32) NOT NULL,           -- llm, embedding
    max_tokens      INT DEFAULT 4096,
    default_params  TEXT,                            -- JSON: {"temperature": 0.7}
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by      VARCHAR(128)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
```

### Usage in SQL

```sql
-- Reference by provider/model
SELECT AI_COMPLETE('OpenAI/gpt-4o', 'Summarize: ' || text) FROM docs;
SELECT AI_SENTIMENT(review_text, MODEL => 'OpenAI/gpt-4o') FROM reviews;
SELECT AI_EMBED('OpenAI/text-embedding-3-large', text) FROM documents;

-- Or use default model for each type
SELECT AI_COMPLETE('Summarize: ' || text) FROM docs;  -- default LLM
SELECT AI_EMBED(text) FROM documents;                  -- default Embedding
```

---

## Complete ML Workflow Example

### Example 1: Sales Forecast (Pure SQL)

```sql
-- Step 1: Prepare training data
CREATE VIEW training_sales AS
SELECT date, SUM(amount) AS total_sales, store_id
FROM orders
WHERE date < '2026-01-01'
GROUP BY date, store_id;

-- Step 2: Train forecast model
CREATE ML_MODEL sales_forecast
    TYPE = FORECAST
    INPUT = (SELECT * FROM training_sales)
    TIMESTAMP = 'date'
    TARGET = 'total_sales'
    SERIES = 'store_id'
    CONFIG = ('method' = 'best');

-- Step 3: Evaluate
SELECT * FROM ML_EVALUATE('sales_forecast');
-- → rmse: 1250.3, mape: 0.08

-- Step 4: Predict next 30 days
CREATE TABLE forecast_results AS
SELECT * FROM ML_FORECAST('sales_forecast', FORECASTING_PERIODS => 30);

-- Step 5: Schedule daily refresh (via StarRocks TASK)
SUBMIT TASK refresh_forecast
    SCHEDULE EVERY(INTERVAL 1 DAY)
AS
    INSERT INTO forecast_history
    SELECT * FROM ML_FORECAST('sales_forecast', FORECASTING_PERIODS => 7);
```

### Example 2: Sentiment Analysis (LLM)

```sql
-- Register model
CREATE AI_MODEL gpt4mini
    ENDPOINT = 'https://api.openai.com/v1'
    MODEL = 'gpt-4o-mini'
    API_KEY = '${OPENAI_API_KEY}';

-- Batch sentiment analysis
CREATE TABLE review_sentiments AS
SELECT
    review_id,
    review_text,
    AI_SENTIMENT(review_text, MODEL => 'gpt4mini') AS sentiment,
    AI_CLASSIFY(review_text, ARRAY('bug', 'feature', 'ux', 'pricing'), MODEL => 'gpt4mini') AS category
FROM product_reviews
WHERE processed = FALSE;
```

### Example 3: Anomaly Detection + LLM Explanation

```sql
-- Train anomaly detector
CREATE ML_MODEL cpu_anomaly
    TYPE = ANOMALY_DETECTION
    INPUT = (SELECT timestamp, cpu_usage FROM server_metrics)
    TIMESTAMP = 'timestamp'
    TARGET = 'cpu_usage';

-- Detect anomalies
CREATE TABLE anomalies AS
SELECT * FROM ML_DETECT_ANOMALIES('cpu_anomaly',
    INPUT => (SELECT timestamp, cpu_usage FROM recent_metrics));

-- LLM explains each anomaly
SELECT
    a.timestamp,
    a.cpu_usage,
    a.is_anomaly,
    AI_COMPLETE('ollama_local',
        PROMPT('CPU spike detected at {0} with value {1}%. Possible cause?', a.timestamp, a.cpu_usage)
    ) AS explanation
FROM anomalies a
WHERE a.is_anomaly = TRUE;
```

---

## Model Training Implementation

Nova ML functions are implemented as **StarRocks UDFs** that call a Python ML service:

```
SQL: CREATE ML_MODEL ... TYPE = FORECAST ...
    → Nova backend receives DDL
    → Extracts training data via SELECT
    → Trains model using Python (Prophet/statsmodels/sklearn)
    → Serializes model (pickle)
    → Stores artifact in object storage (via stage)
    → Registers metadata in NOVA_SYSTEM.ML_MODELS

SQL: SELECT ML_FORECAST('model_name', 30) ...
    → Nova backend receives query
    → Loads model artifact from storage
    → Runs prediction in Python
    → Returns results as table
```

### Python ML Service

```python
# services/ml_engine.py
import pickle
import pandas as pd
from prophet import Prophet
from sklearn.ensemble import GradientBoostingClassifier, IsolationForest

class MLEngine:
    """Python ML engine — trains and serves models."""
    
    def train_forecast(self, data: pd.DataFrame, config: dict) -> bytes:
        """Train forecasting model."""
        method = config.get("method", "best")
        
        if method == "fast":
            # GBM only
            from sklearn.ensemble import GradientBoostingRegressor
            model = GradientBoostingRegressor()
            # ... feature engineering (lags, rolling averages)
            model.fit(X, y)
        else:
            # Ensemble: try Prophet, pick best
            model = Prophet()
            df = data.rename(columns={config["timestamp"]: "ds", config["target"]: "y"})
            model.fit(df)
        
        return pickle.dumps(model)
    
    def predict_forecast(self, model_bytes: bytes, periods: int, config: dict) -> pd.DataFrame:
        """Generate forecast."""
        model = pickle.loads(model_bytes)
        
        if isinstance(model, Prophet):
            future = model.make_future_dataframe(periods=periods)
            forecast = model.predict(future)
            return forecast[['ds', 'yhat', 'yhat_lower', 'yhat_upper']].tail(periods)
        else:
            # GBM prediction
            ...
    
    def train_classification(self, data: pd.DataFrame, target: str, config: dict) -> bytes:
        """Train classification model."""
        model = GradientBoostingClassifier()
        X = data.drop(columns=[target])
        y = data[target]
        model.fit(X, y)
        return pickle.dumps(model)
    
    def predict_classification(self, model_bytes: bytes, data: pd.DataFrame) -> pd.DataFrame:
        """Predict classes."""
        model = pickle.loads(model_bytes)
        predictions = model.predict(data)
        probabilities = model.predict_proba(data)
        return pd.DataFrame({
            'prediction': predictions,
            'probability': probabilities.max(axis=1)
        })
    
    def train_anomaly(self, data: pd.DataFrame, config: dict) -> bytes:
        """Train anomaly detection model."""
        model = IsolationForest(contamination=config.get("contamination", 0.01))
        model.fit(data.select_dtypes(include='number'))
        return pickle.dumps(model)
    
    def detect_anomalies(self, model_bytes: bytes, data: pd.DataFrame) -> pd.DataFrame:
        """Detect anomalies."""
        model = pickle.loads(model_bytes)
        predictions = model.predict(data.select_dtypes(include='number'))
        scores = model.decision_function(data.select_dtypes(include='number'))
        return pd.DataFrame({
            'is_anomaly': predictions == -1,
            'anomaly_score': scores
        })
```

---

## ML Manager UI

```
┌─ Machine Learning ──────────────────────────────────────┐
│                                                          │
│  [Models] [AI Models] [Training] [Predictions]           │
│                                                          │
│  ── ML Models ──                                         │
│  Name            Type           Version  Metrics         │
│  sales_forecast  FORECAST       v1       RMSE=1250      │
│  churn_model     CLASSIFY       v2       AUC=0.89       │
│  cpu_anomaly     ANOMALY_DET    v1       —              │
│                                                          │
│  ── AI Models ──                                         │
│  Name         Endpoint                    Model          │
│  gpt4mini     api.openai.com/v1           gpt-4o-mini    │
│  ollama       localhost:11434/v1          gemma3:12b     │
│  azure_gpt4   my-azure.openai.azure.com  gpt-4o         │
│                                                          │
│  [+ Train New Model]  [+ Register AI Model]              │
└──────────────────────────────────────────────────────────┘
```
