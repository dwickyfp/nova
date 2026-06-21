"""ML Engine service — train, predict, and manage classical ML models.

Models are trained using data fetched from StarRocks SQL queries.
Trained model binaries are stored in NOVA_SYSTEM.ML_MODEL_VERSIONS.model_binary
(base64-encoded joblib pickle). Predictions can run via API or SQL UDF.

Tables:
  NOVA_SYSTEM.ML_MODELS          — model metadata
  NOVA_SYSTEM.ML_MODEL_VERSIONS  — versioned model binaries + metrics
  NOVA_SYSTEM.ML_MODEL_ALIASES   — alias → (model_id, version) mapping
"""

import base64
import json
import logging
from uuid import uuid4

import asyncmy
import asyncmy.cursors
import io
import joblib
import numpy as np
from sklearn.ensemble import (
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.neighbors import KNeighborsClassifier, KNeighborsRegressor
from sklearn.svm import SVC, SVR
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Algorithm registry ────────────────────────────────────────

ALGORITHMS = {
    "classification": {
        "linear": LogisticRegression,
        "logistic": LogisticRegression,
        "decision_tree": DecisionTreeClassifier,
        "random_forest": RandomForestClassifier,
        "gradient_boost": GradientBoostingClassifier,
        "knn": KNeighborsClassifier,
        "svm": SVC,
    },
    "regression": {
        "linear": LinearRegression,
        "decision_tree": DecisionTreeRegressor,
        "random_forest": RandomForestRegressor,
        "gradient_boost": GradientBoostingRegressor,
        "knn": KNeighborsRegressor,
        "svm": SVR,
    },
}


def _pick_algorithm(model_type: str, algorithm: str, n_rows: int) -> str:
    """Auto-select algorithm based on problem type and data size."""
    if algorithm != "auto":
        return algorithm
    if model_type == "classification":
        if n_rows < 1000:
            return "decision_tree"
        elif n_rows < 10000:
            return "random_forest"
        else:
            return "gradient_boost"
    else:  # regression
        if n_rows < 1000:
            return "linear"
        elif n_rows < 10000:
            return "random_forest"
        else:
            return "gradient_boost"


class MLEngineService:
    """Train, predict, and manage classical ML models."""

    # ── DB helpers ──────────────────────────────────────────────

    @staticmethod
    async def _connect() -> asyncmy.Connection:
        """Open a root-level connection to StarRocks."""
        return await asyncmy.connect(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user=settings.STARROCKS_ROOT_USER,
            password=settings.STARROCKS_ROOT_PASSWORD,
            autocommit=True,
            connect_timeout=10,
        )

    # ── Training ────────────────────────────────────────────────

    async def train_model(
        self,
        model_name: str,
        model_type: str,
        algorithm: str,
        training_sql: str,
        target_column: str,
        feature_columns: list[str] | None,
        hyperparameters: dict | None,
        test_size: float,
        database_name: str | None,
        created_by: str = "root",
    ) -> dict:
        """Train a model from SQL query data.

        1. Execute training_sql in StarRocks to fetch data
        2. Train sklearn model on the data
        3. Evaluate on test split
        4. Serialize model to base64, store in ML_MODEL_VERSIONS
        """
        # 1. Fetch training data from StarRocks
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                if database_name:
                    await cur.execute(f"USE {database_name}")
                await cur.execute(training_sql)
                rows = await cur.fetchall()
                columns = (
                    [desc[0] for desc in cur.description]
                    if cur.description
                    else (list(rows[0].keys()) if rows else [])
                )
        finally:
            conn.close()

        if not rows:
            raise ValueError("Training SQL returned no rows")

        n_rows = len(rows)
        logger.info("Training data: %d rows, %d columns", n_rows, len(columns))

        # 2. Determine feature columns
        if not feature_columns:
            feature_columns = [c for c in columns if c != target_column]

        if target_column not in columns:
            raise ValueError(f"Target column '{target_column}' not found in query results")

        # 3. Prepare X (features) and y (target)
        X = []
        y = []
        for row in rows:
            feature_vals = []
            skip = False
            for col in feature_columns:
                val = row.get(col)
                if val is None:
                    skip = True
                    break
                feature_vals.append(float(val) if not isinstance(val, (int, float)) else val)
            if skip:
                continue
            target_val = row.get(target_column)
            if target_val is None:
                continue
            X.append(feature_vals)
            y.append(target_val)

        if len(X) < 10:
            raise ValueError(f"Not enough valid rows for training: {len(X)}. Need at least 10.")

        X = np.array(X, dtype=float)
        y = np.array(y)

        # Encode string labels for classification
        label_encoder = None
        if model_type == "classification" and y.dtype == object:
            from sklearn.preprocessing import LabelEncoder
            label_encoder = LabelEncoder()
            y = label_encoder.fit_transform(y)

        logger.info("Prepared data: X=%s, y=%s, algorithm=%s", X.shape, y.shape, algorithm)

        # 4. Pick algorithm
        chosen_algo = _pick_algorithm(model_type, algorithm, len(X))
        algo_classes = ALGORITHMS.get(model_type, {})
        if chosen_algo not in algo_classes:
            raise ValueError(f"Algorithm '{chosen_algo}' not supported for {model_type}")

        # 5. Train/test split
        if test_size > 0 and len(X) >= 20:
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=test_size, random_state=42, stratify=y if model_type == "classification" else None
            )
        else:
            X_train, X_test, y_train, y_test = X, X, y, y

        # 6. Create and train model
        model_class = algo_classes[chosen_algo]
        model_kwargs = hyperparameters or {}
        model = model_class(**model_kwargs)
        model.fit(X_train, y_train)

        # 7. Evaluate
        y_pred = model.predict(X_test)
        metrics = {}
        if model_type == "classification":
            metrics["accuracy"] = float(accuracy_score(y_test, y_pred))
            if label_encoder:
                target_names = [str(c) for c in label_encoder.classes_]
                report = classification_report(
                    y_test, y_pred, target_names=target_names, output_dict=True, zero_division=0
                )
            else:
                report = classification_report(y_test, y_pred, output_dict=True, zero_division=0)
            metrics["classification_report"] = report
        else:  # regression
            metrics["mse"] = float(mean_squared_error(y_test, y_pred))
            metrics["rmse"] = float(np.sqrt(metrics["mse"]))
            metrics["mae"] = float(mean_absolute_error(y_test, y_pred))
            metrics["r2"] = float(r2_score(y_test, y_pred))

        logger.info("Model trained: %s, metrics=%s", chosen_algo, metrics)

        # 8. Serialize model (include label_encoder if used)
        model_bundle = {"model": model, "feature_columns": feature_columns, "target_column": target_column}
        if label_encoder:
            model_bundle["label_encoder"] = label_encoder
        buf = io.BytesIO()
        joblib.dump(model_bundle, buf)
        model_binary = base64.b64encode(buf.getvalue()).decode("utf-8")

        # 9. Store in database
        model_id = str(uuid4())
        hyperparams_json = json.dumps(hyperparameters or {})
        features_json = json.dumps(feature_columns)
        metrics_json = json.dumps(metrics)

        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                # Insert model metadata
                await cur.execute(
                    """INSERT INTO NOVA_SYSTEM.ML_MODELS
                       (model_id, model_type, model_name, target_column, feature_columns,
                        hyperparameters, training_sql, database_name, created_at, created_by, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, NOW())""",
                    (
                        model_id, model_type, model_name, target_column,
                        features_json, hyperparams_json, training_sql,
                        database_name, created_by,
                    ),
                )

                # Get next version number
                await cur.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM NOVA_SYSTEM.ML_MODEL_VERSIONS WHERE model_id = %s",
                    (model_id,),
                )
                version_row = await cur.fetchone()
                version = version_row[0] if version_row else 1

                # Insert model version with binary
                await cur.execute(
                    """INSERT INTO NOVA_SYSTEM.ML_MODEL_VERSIONS
                       (model_id, version, status, training_rows, metrics, model_binary, created_at, created_by)
                       VALUES (%s, %s, 'active', %s, %s, %s, NOW(), %s)""",
                    (model_id, version, len(X), metrics_json, model_binary, created_by),
                )
        finally:
            conn.close()

        return {
            "model_id": model_id,
            "model_name": model_name,
            "model_type": model_type,
            "algorithm": chosen_algo,
            "version": version,
            "status": "active",
            "training_rows": len(X),
            "feature_columns": feature_columns,
            "metrics": metrics,
            "message": f"Model trained successfully with {chosen_algo}",
        }

    # ── Prediction ──────────────────────────────────────────────

    async def predict(self, model_alias: str, features: dict) -> dict:
        """Run prediction using a model identified by alias."""
        # 1. Resolve alias to model_id + version
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    """SELECT a.model_id, a.version, m.model_name, m.model_type
                       FROM NOVA_SYSTEM.ML_MODEL_ALIASES a
                       JOIN NOVA_SYSTEM.ML_MODELS m ON a.model_id = m.model_id
                       WHERE a.alias_name = %s""",
                    (model_alias,),
                )
                alias_row = await cur.fetchone()
                if not alias_row:
                    raise ValueError(f"Model alias '{model_alias}' not found")

                # Fetch model binary
                await cur.execute(
                    """SELECT model_binary FROM NOVA_SYSTEM.ML_MODEL_VERSIONS
                       WHERE model_id = %s AND version = %s""",
                    (alias_row["model_id"], alias_row["version"]),
                )
                version_row = await cur.fetchone()
                if not version_row:
                    raise ValueError(f"Model version {alias_row['version']} not found")
        finally:
            conn.close()

        # 2. Deserialize model
        model_binary_data = base64.b64decode(version_row["model_binary"])
        model_bundle = joblib.load(io.BytesIO(model_binary_data))
        model = model_bundle["model"]
        feature_columns = model_bundle["feature_columns"]
        label_encoder = model_bundle.get("label_encoder")

        # 3. Build feature vector in correct order
        feature_vector = []
        for col in feature_columns:
            if col not in features:
                raise ValueError(f"Missing feature column: {col}")
            feature_vector.append(float(features[col]))

        # 4. Predict
        X = np.array([feature_vector], dtype=float)
        prediction = model.predict(X)[0]

        # Decode label if classification with encoder
        if label_encoder is not None:
            prediction = label_encoder.inverse_transform([prediction])[0]

        # Get probability for classification
        probability = None
        if hasattr(model, "predict_proba"):
            try:
                proba = model.predict_proba(X)[0]
                classes = (
                    label_encoder.inverse_transform(model.classes_)
                    if label_encoder is not None
                    else model.classes_
                )
                probability = {str(c): float(p) for c, p in zip(classes, proba)}
            except Exception:
                pass

        return {
            "model_alias": model_alias,
            "model_name": alias_row["model_name"],
            "prediction": prediction if not isinstance(prediction, np.generic) else prediction.item(),
            "probability": probability,
            "model_version": alias_row["version"],
        }

    async def batch_predict(
        self, model_alias: str, prediction_sql: str, database_name: str | None
    ) -> dict:
        """Run batch predictions using features from a SQL query."""
        # 1. Resolve alias and load model
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    """SELECT a.model_id, a.version, m.model_name, m.model_type
                       FROM NOVA_SYSTEM.ML_MODEL_ALIASES a
                       JOIN NOVA_SYSTEM.ML_MODELS m ON a.model_id = m.model_id
                       WHERE a.alias_name = %s""",
                    (model_alias,),
                )
                alias_row = await cur.fetchone()
                if not alias_row:
                    raise ValueError(f"Model alias '{model_alias}' not found")

                await cur.execute(
                    """SELECT model_binary FROM NOVA_SYSTEM.ML_MODEL_VERSIONS
                       WHERE model_id = %s AND version = %s""",
                    (alias_row["model_id"], alias_row["version"]),
                )
                version_row = await cur.fetchone()
                if not version_row:
                    raise ValueError(f"Model version not found")

                # 2. Fetch prediction data
                if database_name:
                    await cur.execute(f"USE {database_name}")
                await cur.execute(prediction_sql)
                rows = await cur.fetchall()
        finally:
            conn.close()

        if not rows:
            return {
                "model_alias": model_alias,
                "model_name": alias_row["model_name"],
                "predictions": [],
                "total_rows": 0,
            }

        # 3. Deserialize model
        model_binary_data = base64.b64decode(version_row["model_binary"])
        model_bundle = joblib.load(io.BytesIO(model_binary_data))
        model = model_bundle["model"]
        feature_columns = model_bundle["feature_columns"]
        label_encoder = model_bundle.get("label_encoder")

        # 4. Batch predict
        X = []
        valid_rows = []
        for row in rows:
            feature_vals = []
            skip = False
            for col in feature_columns:
                val = row.get(col)
                if val is None:
                    skip = True
                    break
                feature_vals.append(float(val) if not isinstance(val, (int, float)) else val)
            if skip:
                continue
            X.append(feature_vals)
            valid_rows.append(row)

        predictions = []
        if X:
            X_array = np.array(X, dtype=float)
            preds = model.predict(X_array)
            if label_encoder is not None:
                preds = label_encoder.inverse_transform(preds)

            for row, pred in zip(valid_rows, preds):
                pred_val = pred if not isinstance(pred, np.generic) else pred.item()
                result = dict(row)
                result["prediction"] = pred_val
                predictions.append(result)

        return {
            "model_alias": model_alias,
            "model_name": alias_row["model_name"],
            "predictions": predictions,
            "total_rows": len(predictions),
        }

    # ── Model Management ────────────────────────────────────────

    async def list_models(self) -> list[dict]:
        """List all models with latest version info."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    """SELECT m.model_id, m.model_name, m.model_type, m.target_column,
                              m.feature_columns, m.hyperparameters, m.training_sql,
                              m.database_name, m.created_at, m.created_by,
                              v.version as latest_version, v.status as latest_status,
                              v.metrics as latest_metrics, v.training_rows
                       FROM NOVA_SYSTEM.ML_MODELS m
                       LEFT JOIN NOVA_SYSTEM.ML_MODEL_VERSIONS v
                         ON m.model_id = v.model_id
                         AND v.version = (
                             SELECT MAX(version) FROM NOVA_SYSTEM.ML_MODEL_VERSIONS
                             WHERE model_id = m.model_id
                         )
                       ORDER BY m.created_at DESC"""
                )
                rows = await cur.fetchall()
        finally:
            conn.close()

        return [self._deserialize_model(row) for row in rows]

    async def get_model(self, model_id: str) -> dict | None:
        """Get model detail with all versions."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    """SELECT * FROM NOVA_SYSTEM.ML_MODELS WHERE model_id = %s""",
                    (model_id,),
                )
                model_row = await cur.fetchone()
                if not model_row:
                    return None

                await cur.execute(
                    """SELECT version, status, training_rows, metrics, created_at, created_by
                       FROM NOVA_SYSTEM.ML_MODEL_VERSIONS
                       WHERE model_id = %s ORDER BY version DESC""",
                    (model_id,),
                )
                version_rows = await cur.fetchall()
        finally:
            conn.close()

        model = self._deserialize_model(model_row)
        versions = []
        for v in version_rows:
            v_dict = dict(v)
            if v_dict.get("metrics") and isinstance(v_dict["metrics"], str):
                try:
                    v_dict["metrics"] = json.loads(v_dict["metrics"])
                except (json.JSONDecodeError, TypeError):
                    pass
            if v_dict.get("created_at"):
                v_dict["created_at"] = str(v_dict["created_at"])
            versions.append(v_dict)

        return {"model": model, "versions": versions}

    async def delete_model(self, model_id: str) -> dict:
        """Delete a model and all its versions."""
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                # Delete aliases
                await cur.execute(
                    "DELETE FROM NOVA_SYSTEM.ML_MODEL_ALIASES WHERE model_id = %s",
                    (model_id,),
                )
                # Delete versions
                await cur.execute(
                    "DELETE FROM NOVA_SYSTEM.ML_MODEL_VERSIONS WHERE model_id = %s",
                    (model_id,),
                )
                # Delete model
                await cur.execute(
                    "DELETE FROM NOVA_SYSTEM.ML_MODELS WHERE model_id = %s",
                    (model_id,),
                )
        finally:
            conn.close()

        return {"model_id": model_id, "deleted": True, "message": "Model deleted"}

    # ── Aliases ─────────────────────────────────────────────────

    async def list_aliases(self) -> list[dict]:
        """List all model aliases."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    """SELECT a.alias_name, a.model_id, a.version, a.created_at,
                              m.model_name
                       FROM NOVA_SYSTEM.ML_MODEL_ALIASES a
                       LEFT JOIN NOVA_SYSTEM.ML_MODELS m ON a.model_id = m.model_id
                       ORDER BY a.alias_name"""
                )
                rows = await cur.fetchall()
        finally:
            conn.close()

        result = []
        for r in rows:
            d = dict(r)
            if d.get("created_at"):
                d["created_at"] = str(d["created_at"])
            result.append(d)
        return result

    async def create_alias(self, alias_name: str, model_id: str, version: int) -> dict:
        """Create or update a model alias."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                # Upsert alias (StarRocks supports PRIMARY KEY upsert)
                await cur.execute(
                    """INSERT INTO NOVA_SYSTEM.ML_MODEL_ALIASES (alias_name, model_id, version, created_at, updated_at)
                       VALUES (%s, %s, %s, NOW(), NOW())""",
                    (alias_name, model_id, version),
                )

                await cur.execute(
                    """SELECT a.alias_name, a.model_id, a.version, a.created_at, m.model_name
                       FROM NOVA_SYSTEM.ML_MODEL_ALIASES a
                       LEFT JOIN NOVA_SYSTEM.ML_MODELS m ON a.model_id = m.model_id
                       WHERE a.alias_name = %s""",
                    (alias_name,),
                )
                row = await cur.fetchone()
        finally:
            conn.close()

        if not row:
            raise ValueError("Failed to create alias")
        d = dict(row)
        if d.get("created_at"):
            d["created_at"] = str(d["created_at"])
        return d

    async def delete_alias(self, alias_name: str) -> dict:
        """Delete a model alias."""
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM NOVA_SYSTEM.ML_MODEL_ALIASES WHERE alias_name = %s",
                    (alias_name,),
                )
        finally:
            conn.close()
        return {"alias_name": alias_name, "deleted": True}

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _deserialize_model(row: dict) -> dict:
        """Deserialize a model row, parsing JSON fields."""
        result = dict(row)
        for field in ("feature_columns", "hyperparameters", "latest_metrics"):
            val = result.get(field)
            if val and isinstance(val, str):
                try:
                    result[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
        if result.get("created_at"):
            result["created_at"] = str(result["created_at"])
        return result


# Singleton
ml_engine_service = MLEngineService()
