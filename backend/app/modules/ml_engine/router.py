"""ML Engine API router — model training, prediction, and management endpoints.

Endpoints under /api/v1/ml:
  POST   /train                    → train a new model
  POST   /predict                  → single prediction
  POST   /predict/batch            → batch prediction via SQL
  GET    /models                   → list all models
  GET    /models/{model_id}        → model detail with versions
  DELETE /models/{model_id}        → delete model
  GET    /aliases                  → list model aliases
  POST   /aliases                  → create/update alias
  DELETE /aliases/{alias_name}     → delete alias
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_current_user
from app.core.security import decrypt_password
from app.modules.ml_engine.schemas import (
    BatchPredictRequest,
    BatchPredictResponse,
    DeleteModelResponse,
    ForecastRequest,
    ForecastResponse,
    MLExecuteRequest,
    MLExecuteResponse,
    ModelAliasCreate,
    ModelAliasListResponse,
    ModelAliasResponse,
    ModelDetailResponse,
    ModelListResponse,
    PredictRequest,
    PredictResponse,
    PromoteRunRequest,
    TrainModelRequest,
    TrainModelResponse,
    VersionForecastRequest,
    VersionPredictRequest,
    VersionPredictResponse,
)
from app.modules.ml_engine.service import ml_engine_service
from app.modules.ml_engine.spec import MLExecutionSpec, MLMode, MLSecurityContext, MLTask
from app.modules.query.sql_pipeline import redact_for_output

router = APIRouter()

# Built once so routes can use a module-level dependency instead of calling
# `Depends(...)` in argument defaults (ruff B008). Same pattern as
# `users/router.py`.
require_user = Depends(get_current_user)


def _tenant_options(user: dict) -> dict:
    tenant = user.get("tenant", "default")
    return {"tenant": tenant} if tenant != "default" else {}


def _caller_credentials(user: dict) -> str:
    """The decrypted StarRocks password from the caller's session.

    ML routes that run caller-supplied SQL must forward this so the statement
    executes on the caller's engine connection. A session whose credential
    cannot be decrypted fails closed with 401 rather than letting the service
    fall back to the root connection (NOVA-104, NOVA-118).
    """
    try:
        return decrypt_password(user["encrypted_password"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=401,
            detail="No user connection is available for this request",
        ) from exc


# ── Training ──────────────────────────────────────────────────


@router.post("/train", response_model=TrainModelResponse)
async def train_model(
    req: TrainModelRequest,
    user: dict = require_user,
):
    """Train a classical ML model using data from a SQL query."""
    password = _caller_credentials(user)
    try:
        result = await ml_engine_service.train_model(
            model_name=req.model_name,
            model_type=req.model_type,
            algorithm=req.algorithm,
            training_sql=req.training_sql,
            target_column=req.target_column,
            feature_columns=req.feature_columns,
            hyperparameters=req.hyperparameters,
            test_size=req.test_size,
            database_name=req.database_name,
            created_by=user["username"],
            username=user["username"],
            password=password,
            role=user.get("active_role"),
            timestamp_column=req.timestamp_column,
            series_column=req.series_column,
            horizon=req.horizon,
            frequency=req.frequency,
            mode=req.mode,
            **_tenant_options(user),
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=redact_for_output(str(e))) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Training failed: {redact_for_output(str(e))}",
        ) from e


# ── Prediction ────────────────────────────────────────────────


@router.post("/predict", response_model=PredictResponse)
async def predict(
    req: PredictRequest,
    user: dict = require_user,
):
    """Run a single prediction using a trained model."""
    try:
        result = await ml_engine_service.predict(
            req.model_alias,
            req.features,
            owner_name=user["username"],
            database_name=req.database_name,
            **_tenant_options(user),
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=redact_for_output(str(e))) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Prediction failed: {redact_for_output(str(e))}",
        ) from e


@router.post("/predict/version", response_model=VersionPredictResponse)
async def predict_version(req: VersionPredictRequest, user: dict = require_user):
    """Run one explicit immutable version for reproducible inference."""
    try:
        return await ml_engine_service.predict_version(
            req.model_id,
            req.version,
            req.features,
            owner_name=user["username"],
            database_name=req.database_name,
            **_tenant_options(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=redact_for_output(str(exc))) from exc


@router.post("/predict/batch", response_model=BatchPredictResponse)
async def batch_predict(
    req: BatchPredictRequest,
    user: dict = require_user,
):
    """Run batch predictions using features from a SQL query."""
    # `prediction_sql` is caller-supplied and must run on the caller's StarRocks
    # connection; forwarding no identity let it execute as root, exposing
    # cross-tenant and system data and allowing DDL/DML/SET ROLE as a superuser
    # (NOVA-118). `_caller_credentials` fails closed when the session carries no
    # readable credential.
    password = _caller_credentials(user)
    try:
        result = await ml_engine_service.batch_predict(
            model_alias=req.model_alias,
            prediction_sql=req.prediction_sql,
            database_name=req.database_name,
            username=user["username"],
            password=password,
            role=user.get("active_role"),
            **_tenant_options(user),
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=redact_for_output(str(e))) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Batch prediction failed: {redact_for_output(str(e))}",
        ) from e


@router.post("/forecast", response_model=ForecastResponse)
async def forecast(req: ForecastRequest, user: dict = require_user):
    """Forecast a future horizon from a persisted forecast alias."""
    try:
        return await ml_engine_service.forecast_alias(
            req.model_alias,
            req.horizon,
            owner_name=user["username"],
            database_name=req.database_name,
            level=req.confidence_level,
            series=req.series,
            **_tenant_options(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=redact_for_output(str(exc))) from exc


@router.post("/predict/materialize")
async def materialize_prediction(req: BatchPredictRequest, user: dict = require_user):
    from app.common.audit import write_audit_log

    security = MLSecurityContext(
        username=user["username"],
        password=_caller_credentials(user),
        database=req.database_name,
        role=user.get("active_role"),
        tenant=user.get("tenant", "default"),
        security_context_version=user.get("security_context_version", 1),
    )
    try:
        result = await ml_engine_service.materialize_prediction(
            req.model_alias, req.prediction_sql, security
        )
    except Exception as exc:
        await write_audit_log(
            event_type="ml",
            user_name=user["username"],
            action="materialize_prediction",
            object_type="ml_model",
            object_name=req.model_alias,
            status="ERROR",
            error_message=redact_for_output(str(exc)),
        )
        raise HTTPException(status_code=400, detail=redact_for_output(str(exc))) from exc
    await write_audit_log(
        event_type="ml",
        user_name=user["username"],
        action="materialize_prediction",
        object_type="ml_model",
        object_name=req.model_alias,
        status="SUCCESS",
        rows_affected=result["total_rows"],
    )
    return result


@router.get("/results/{run_id}")
async def result_page(
    run_id: str,
    part: int = 0,
    database_name: str | None = None,
    schema_name: str | None = None,
    user: dict = require_user,
):
    try:
        return await ml_engine_service.result_page(
            run_id,
            part,
            MLSecurityContext(
                username=user["username"],
                password="",
                database=database_name,
                schema=schema_name,
                role=user.get("active_role"),
                tenant=user.get("tenant", "default"),
                security_context_version=user.get("security_context_version", 1),
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=redact_for_output(str(exc))) from exc


@router.post("/forecast/version", response_model=ForecastResponse)
async def forecast_version(req: VersionForecastRequest, user: dict = require_user):
    """Forecast from one immutable persisted model version."""
    try:
        return await ml_engine_service.forecast_version(
            req.model_id,
            req.version,
            req.horizon,
            owner_name=user["username"],
            database_name=req.database_name,
            level=req.confidence_level,
            series=req.series,
            **_tenant_options(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=redact_for_output(str(exc))) from exc


# ── Model Management ──────────────────────────────────────────


@router.get("/models", response_model=ModelListResponse)
async def list_models(
    database_name: str | None = None,
    user: dict = require_user,
):
    """List all trained models."""
    models = await ml_engine_service.list_models(
        owner_name=user["username"], database_name=database_name, **_tenant_options(user)
    )
    return {"models": models, "count": len(models)}


@router.get("/models/{model_id}", response_model=ModelDetailResponse)
async def get_model(
    model_id: str,
    database_name: str | None = None,
    user: dict = require_user,
):
    """Get model detail with all versions."""
    result = await ml_engine_service.get_model(
        model_id, owner_name=user["username"], database_name=database_name, **_tenant_options(user)
    )
    if not result:
        raise HTTPException(status_code=404, detail="Model not found")
    return result


@router.delete("/models/{model_id}", response_model=DeleteModelResponse)
async def delete_model(
    model_id: str,
    database_name: str | None = None,
    user: dict = require_user,
):
    """Delete a model and all its versions."""
    try:
        return await ml_engine_service.delete_model(
            model_id,
            owner_name=user["username"],
            database_name=database_name,
            **_tenant_options(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=redact_for_output(str(exc))) from exc


# ── Aliases ───────────────────────────────────────────────────


@router.get("/aliases", response_model=ModelAliasListResponse)
async def list_aliases(
    database_name: str | None = None,
    user: dict = require_user,
):
    """List all model aliases."""
    aliases = await ml_engine_service.list_aliases(
        owner_name=user["username"], database_name=database_name, **_tenant_options(user)
    )
    return {"aliases": aliases, "count": len(aliases)}


@router.post("/aliases", response_model=ModelAliasResponse)
async def create_alias(
    req: ModelAliasCreate,
    user: dict = require_user,
):
    """Create or update a model alias."""
    try:
        return await ml_engine_service.create_alias(
            req.alias_name,
            req.model_id,
            req.version,
            owner_name=user["username"],
            database_name=req.database_name,
            **_tenant_options(user),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=redact_for_output(str(e))) from e


@router.delete("/aliases/{alias_name}")
async def delete_alias(
    alias_name: str,
    database_name: str | None = None,
    user: dict = require_user,
):
    """Delete a model alias."""
    return await ml_engine_service.delete_alias(
        alias_name,
        owner_name=user["username"],
        database_name=database_name,
        **_tenant_options(user),
    )


@router.post("/execute", response_model=MLExecuteResponse)
async def execute_ml(req: MLExecuteRequest, user: dict = require_user):
    """Execute deterministic persistent or ephemeral ML as the requesting user."""
    password = _caller_credentials(user)
    try:
        result = await ml_engine_service.execute(
            MLExecutionSpec(
                task=MLTask(req.task),
                input_sql=req.input_sql,
                security=MLSecurityContext(
                    username=user["username"],
                    password=password,
                    database=req.database_name,
                    schema=req.schema_name,
                    role=user.get("active_role"),
                    tenant=user.get("tenant", "default"),
                    security_context_version=user.get("security_context_version", 1),
                ),
                mode=MLMode(req.mode),
                persist=req.persist,
                model_name=req.model_name,
                algorithm=req.algorithm,
                feature_columns=tuple(req.feature_columns or ()),
                target_column=req.target_column,
                timestamp_column=req.timestamp_column,
                series_column=req.series_column,
                row_identifier=req.row_identifier,
                horizon=req.horizon,
                frequency=req.frequency,
                metric=req.metric,
                parameters=req.parameters,
            )
        )
        return result.__dict__
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=redact_for_output(str(exc))) from exc


@router.post("/runs/{run_id}/promote", response_model=MLExecuteResponse)
async def promote_run(
    run_id: str,
    req: PromoteRunRequest,
    user: dict = require_user,
):
    """Promote a live ephemeral run without retraining."""
    result = await ml_engine_service.promote(
        run_id,
        model_name=req.model_name,
        security=MLSecurityContext(
            username=user["username"],
            password=_caller_credentials(user),
            database=req.database_name,
            schema=req.schema_name,
            role=user.get("active_role"),
            tenant=user.get("tenant", "default"),
            security_context_version=user.get("security_context_version", 1),
        ),
    )
    return result.__dict__
