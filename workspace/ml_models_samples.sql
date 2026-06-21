-- ============================================================================
-- Nova ML Models - Sample Usage SQL
-- ============================================================================
-- This file demonstrates how to train, predict, and manage classical ML models
-- using Nova's ML Engine. Models are trained from SQL queries and stored
-- in NOVA_SYSTEM.ML_MODELS / ML_MODEL_VERSIONS.
--
-- PREREQUISITES:
--   1. Nova backend running on port 8000
--   2. scikit-learn, joblib, numpy installed in backend
--   3. Auth token from POST /api/v1/auth/login
--
-- API ENDPOINTS:
--   POST   /api/v1/ml/train           → Train model from SQL query
--   POST   /api/v1/ml/predict         → Single prediction
--   POST   /api/v1/ml/predict/batch   → Batch prediction via SQL
--   GET    /api/v1/ml/models          → List all models
--   GET    /api/v1/ml/models/{id}     → Model detail with versions
--   DELETE /api/v1/ml/models/{id}     → Delete model
--   GET    /api/v1/ml/aliases         → List model aliases
--   POST   /api/v1/ml/aliases         → Create/update alias
--   DELETE /api/v1/ml/aliases/{name}  → Delete alias
--
-- SQL UDF:
--   ML_PREDICT(model_alias, features_json) — placeholder that returns API hint
--
-- SUPPORTED ALGORITHMS:
--   Classification: logistic, decision_tree, random_forest, gradient_boost, knn, svm
--   Regression:     linear, decision_tree, random_forest, gradient_boost, knn, svm
--   Use "auto" to let Nova pick the best algorithm based on data size
-- ============================================================================

-- ============================================================================
-- SECTION 0: Verify ML UDF is registered
-- ============================================================================
SHOW GLOBAL FUNCTIONS;
-- Expected: includes ml_predict

-- ML_PREDICT is a placeholder UDF that points to the API
SELECT ML_PREDICT('status_predictor', '{"total_amount": 150}');
-- Returns: Use POST /api/v1/ml/predict with {"model_alias":"status_predictor","features":{"total_amount":150}}


-- ============================================================================
-- SECTION 1: Train a Classification Model
-- ============================================================================
-- Train a model to predict order status based on total_amount
-- Execute via: curl -X POST http://localhost:8000/api/v1/ml/train

/*
{
  "model_name": "order_status_predictor",
  "model_type": "classification",
  "algorithm": "auto",
  "training_sql": "SELECT total_amount, status FROM NOVA_EXAMPLE.orders WHERE status IS NOT NULL",
  "target_column": "status",
  "feature_columns": ["total_amount"],
  "test_size": 0.2
}

Response:
{
  "model_id": "2436afcc-...",
  "model_name": "order_status_predictor",
  "model_type": "classification",
  "algorithm": "decision_tree",
  "version": 1,
  "status": "active",
  "training_rows": 12,
  "metrics": {
    "accuracy": 0.9167,
    "classification_report": { ... }
  }
}
*/


-- ============================================================================
-- SECTION 2: Train a Regression Model
-- ============================================================================
-- Train a model to predict order total based on customer and product data
-- Execute via: curl -X POST http://localhost:8000/api/v1/ml/train

/*
{
  "model_name": "order_total_predictor",
  "model_type": "regression",
  "algorithm": "random_forest",
  "training_sql": "SELECT c.customer_id, o.order_id, o.total_amount, o.status FROM NOVA_EXAMPLE.orders o JOIN NOVA_EXAMPLE.customers c ON o.customer_id = c.customer_id",
  "target_column": "total_amount",
  "feature_columns": ["customer_id", "order_id"],
  "test_size": 0.2,
  "hyperparameters": {"n_estimators": 50}
}
*/


-- ============================================================================
-- SECTION 3: Create Model Alias
-- ============================================================================
-- After training, create an alias to use for predictions
-- Execute via: curl -X POST http://localhost:8000/api/v1/ml/aliases

/*
{
  "alias_name": "status_predictor",
  "model_id": "2436afcc-f452-4e6e-9a34-c1c3407adf63",
  "version": 1
}
*/


-- ============================================================================
-- SECTION 4: Single Prediction
-- ============================================================================
-- Predict using feature values
-- Execute via: curl -X POST http://localhost:8000/api/v1/ml/predict

/*
{
  "model_alias": "status_predictor",
  "features": {"total_amount": 150.00}
}

Response:
{
  "model_alias": "status_predictor",
  "model_name": "order_status_predictor",
  "prediction": "COMPLETED",
  "probability": {
    "COMPLETED": 1.0,
    "PENDING": 0.0,
    "SHIPPED": 0.0
  },
  "model_version": 1
}
*/


-- ============================================================================
-- SECTION 5: Batch Prediction via SQL
-- ============================================================================
-- Run predictions on entire dataset using a SQL query
-- Execute via: curl -X POST http://localhost:8000/api/v1/ml/predict/batch

/*
{
  "model_alias": "status_predictor",
  "prediction_sql": "SELECT total_amount FROM NOVA_EXAMPLE.orders LIMIT 5"
}

Response:
{
  "model_alias": "status_predictor",
  "predictions": [
    {"total_amount": 24498000.00, "prediction": "SHIPPED"},
    {"total_amount": 4999000.00, "prediction": "COMPLETED"},
    ...
  ],
  "total_rows": 5
}
*/


-- ============================================================================
-- SECTION 6: List All Models
-- ============================================================================
-- Execute via: curl http://localhost:8000/api/v1/ml/models

/*
Returns all trained models with latest version metrics:
{
  "models": [
    {
      "model_id": "...",
      "model_name": "order_status_predictor",
      "model_type": "classification",
      "target_column": "status",
      "feature_columns": ["total_amount"],
      "latest_version": 1,
      "latest_status": "active",
      "latest_metrics": {"accuracy": 0.9167, ...},
      "training_rows": 12
    }
  ],
  "count": 1
}
*/


-- ============================================================================
-- SECTION 7: Retrain Model (New Version)
-- ============================================================================
-- Train the same model name again to create a new version
-- Execute via: curl -X POST http://localhost:8000/api/v1/ml/train

/*
{
  "model_name": "order_status_predictor",
  "model_type": "classification",
  "algorithm": "random_forest",
  "training_sql": "SELECT total_amount, status FROM NOVA_EXAMPLE.orders WHERE status IS NOT NULL",
  "target_column": "status",
  "feature_columns": ["total_amount"],
  "test_size": 0.2,
  "hyperparameters": {"n_estimators": 100, "max_depth": 5}
}

This creates version 2 of the same model.
Update alias to point to new version:
curl -X POST http://localhost:8000/api/v1/ml/aliases
{
  "alias_name": "status_predictor",
  "model_id": "...",
  "version": 2
}
*/


-- ============================================================================
-- SECTION 8: Advanced — Multi-Feature Classification
-- ============================================================================
-- Train with multiple features from joined tables

/*
{
  "model_name": "customer_segment_classifier",
  "model_type": "classification",
  "algorithm": "gradient_boost",
  "training_sql": "SELECT c.customer_id, c.country, o.total_amount, o.status FROM NOVA_EXAMPLE.customers c JOIN NOVA_EXAMPLE.orders o ON c.customer_id = o.customer_id",
  "target_column": "status",
  "feature_columns": ["customer_id", "total_amount"],
  "test_size": 0.25,
  "hyperparameters": {"n_estimators": 100, "learning_rate": 0.1}
}
*/


-- ============================================================================
-- SECTION 9: Query ML Tables Directly
-- ============================================================================

-- List all trained models
SELECT model_id, model_name, model_type, target_column, created_at
FROM NOVA_SYSTEM.ML_MODELS
ORDER BY created_at DESC;

-- List model versions with metrics
SELECT m.model_name, v.version, v.status, v.training_rows, v.metrics, v.created_at
FROM NOVA_SYSTEM.ML_MODELS m
JOIN NOVA_SYSTEM.ML_MODEL_VERSIONS v ON m.model_id = v.model_id
ORDER BY m.model_name, v.version DESC;

-- List model aliases
SELECT a.alias_name, m.model_name, a.version, a.created_at
FROM NOVA_SYSTEM.ML_MODEL_ALIASES a
JOIN NOVA_SYSTEM.ML_MODELS m ON a.model_id = m.model_id
ORDER BY a.alias_name;

-- Check model training SQL
SELECT model_name, model_type, training_sql, feature_columns
FROM NOVA_SYSTEM.ML_MODELS;


-- ============================================================================
-- SECTION 10: Best Practices
-- ============================================================================
--
-- 1. DATA QUALITY:
--    Ensure training SQL returns clean numeric data. Null values are skipped.
--    At least 10 valid rows required for training.
--
-- 2. FEATURE ENGINEERING:
--    Pre-compute features in the training SQL using CASE, CAST, etc.
--    All features must be numeric (float/int). Encode categoricals first.
--
-- 3. ALGORITHM SELECTION:
--    - Small data (<1000 rows): decision_tree or linear
--    - Medium data (1000-10000): random_forest
--    - Large data (>10000): gradient_boost
--    - Use "auto" to let Nova decide
--
-- 4. MODEL VERSIONING:
--    Retraining creates a new version. Point alias to new version for rollout.
--    Keep old versions for rollback.
--
-- 5. BATCH PREDICTIONS:
--    Use /predict/batch for bulk predictions. More efficient than single calls.
--    The prediction_sql should return ONLY feature columns.
--
-- 6. HYPERPARAMETERS:
--    Pass algorithm-specific params via "hyperparameters" field.
--    Example: {"n_estimators": 100, "max_depth": 5} for random_forest
--
-- ============================================================================

-- End of ML Models Sample SQL
-- ============================================================================
