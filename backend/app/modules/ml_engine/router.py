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
from app.modules.ml_engine.schemas import (
    BatchPredictRequest,
    BatchPredictResponse,
    DeleteModelResponse,
    ModelAliasCreate,
    ModelAliasListResponse,
    ModelAliasResponse,
    ModelDetailResponse,
    ModelListResponse,
    PredictRequest,
    PredictResponse,
    TrainModelRequest,
    TrainModelResponse,
)
from app.modules.ml_engine.service import ml_engine_service

router = APIRouter()


# ── Training ──────────────────────────────────────────────────


@router.post("/train", response_model=TrainModelResponse)
async def train_model(
    req: TrainModelRequest,
    user: dict = Depends(get_current_user),
):
    """Train a classical ML model using data from a SQL query."""
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
            created_by=user.get("username", "root"),
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Training failed: {e}")


# ── Prediction ────────────────────────────────────────────────


@router.post("/predict", response_model=PredictResponse)
async def predict(
    req: PredictRequest,
    user: dict = Depends(get_current_user),
):
    """Run a single prediction using a trained model."""
    try:
        result = await ml_engine_service.predict(req.model_alias, req.features)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction failed: {e}")


@router.post("/predict/batch", response_model=BatchPredictResponse)
async def batch_predict(
    req: BatchPredictRequest,
    user: dict = Depends(get_current_user),
):
    """Run batch predictions using features from a SQL query."""
    try:
        result = await ml_engine_service.batch_predict(
            req.model_alias, req.prediction_sql, req.database_name
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Batch prediction failed: {e}")


# ── Model Management ──────────────────────────────────────────


@router.get("/models", response_model=ModelListResponse)
async def list_models(
    user: dict = Depends(get_current_user),
):
    """List all trained models."""
    models = await ml_engine_service.list_models()
    return {"models": models, "count": len(models)}


@router.get("/models/{model_id}", response_model=ModelDetailResponse)
async def get_model(
    model_id: str,
    user: dict = Depends(get_current_user),
):
    """Get model detail with all versions."""
    result = await ml_engine_service.get_model(model_id)
    if not result:
        raise HTTPException(status_code=404, detail="Model not found")
    return result


@router.delete("/models/{model_id}", response_model=DeleteModelResponse)
async def delete_model(
    model_id: str,
    user: dict = Depends(get_current_user),
):
    """Delete a model and all its versions."""
    return await ml_engine_service.delete_model(model_id)


# ── Aliases ───────────────────────────────────────────────────


@router.get("/aliases", response_model=ModelAliasListResponse)
async def list_aliases(
    user: dict = Depends(get_current_user),
):
    """List all model aliases."""
    aliases = await ml_engine_service.list_aliases()
    return {"aliases": aliases, "count": len(aliases)}


@router.post("/aliases", response_model=ModelAliasResponse)
async def create_alias(
    req: ModelAliasCreate,
    user: dict = Depends(get_current_user),
):
    """Create or update a model alias."""
    try:
        return await ml_engine_service.create_alias(req.alias_name, req.model_id, req.version)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/aliases/{alias_name}")
async def delete_alias(
    alias_name: str,
    user: dict = Depends(get_current_user),
):
    """Delete a model alias."""
    return await ml_engine_service.delete_alias(alias_name)
