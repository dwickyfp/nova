"""ML Engine internal router — machine-to-machine ML prediction.

This surface exists for the StarRocks UDF bridge, which has no Nova user
session and therefore cannot carry a JWT. It is authenticated with a
pre-shared secret instead: the caller must send the token in
``X-Nova-Internal-Token``, and the request is only accepted from a loopback
peer (see ``internal_auth.py``).

The endpoint is fail-closed. If the deployment has not configured the secret,
every request is rejected — the route never falls back to unauthenticated.
Standalone StarRocks Java UDFs cannot read Nova's environment, so there is no
unauthenticated fallback for them either; they must use the authenticated
``/api/v1/ml/predict`` route or a trusted proxy configured with the secret.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.modules.ml_engine.internal_auth import require_internal_caller
from app.modules.ml_engine.schemas import PredictRequest, PredictResponse
from app.modules.ml_engine.service import ml_engine_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/predict", response_model=PredictResponse)
async def internal_predict(
    req: PredictRequest,
    request: Request,
    _caller: None = Depends(require_internal_caller),
):
    """Single prediction over the authenticated internal channel."""
    client_ip = request.client.host if request.client else "unknown"
    logger.debug("Internal predict called from %s", client_ip)
    try:
        result = await ml_engine_service.predict(req.model_alias, req.features)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction failed: {e}") from e
