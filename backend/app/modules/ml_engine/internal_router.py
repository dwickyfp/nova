"""ML Engine internal router -- no auth, localhost-only for Java UDF calls."""

import logging
from fastapi import APIRouter, HTTPException, Request
from app.modules.ml_engine.schemas import PredictRequest, PredictResponse
from app.modules.ml_engine.service import ml_engine_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/predict", response_model=PredictResponse)
async def internal_predict(req: PredictRequest, request: Request):
    """Single prediction -- no auth, for Java UDF calls from StarRocks BE."""
    client_ip = request.client.host if request.client else "unknown"
    logger.debug("Internal predict called from %s", client_ip)
    try:
        result = await ml_engine_service.predict(req.model_alias, req.features)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction failed: {e}")
