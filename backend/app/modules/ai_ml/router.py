"""AI Provider Management API router — provider and model CRUD endpoints.

Endpoints under /api/v1/ai:
  GET    /providers                  → list all providers
  POST   /providers                 → create provider
  DELETE /providers/{id}            → delete provider (cascades models)
  GET    /providers/{id}/models     → list models for provider
  POST   /providers/{id}/models     → create model
  DELETE /models/{id}               → delete model
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_current_user
from app.modules.ai_ml.schemas import (
    AIModelCreate,
    AIModelListResponse,
    AIModelResponse,
    AIModelUpdate,
    AIProviderCreate,
    AIProviderListResponse,
    AIProviderResponse,
    AIProviderUpdate,
    TestConnectionRequest,
    TestConnectionResponse,
)
from app.modules.ai_ml.service import ai_service

router = APIRouter()


# ── Providers ──────────────────────────────────────────────────


@router.get("/providers", response_model=AIProviderListResponse)
async def list_providers(
    user: dict = Depends(get_current_user),
):
    """List all registered AI providers."""
    providers = await ai_service.list_providers()
    provider_responses = [AIProviderResponse(**p) for p in providers]
    return AIProviderListResponse(providers=provider_responses, count=len(provider_responses))


@router.post("/providers", response_model=AIProviderResponse, status_code=201)
async def create_provider(
    body: AIProviderCreate,
    user: dict = Depends(get_current_user),
):
    """Create a new AI provider."""
    try:
        result = await ai_service.create_provider(body.model_dump(), user["username"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return AIProviderResponse(**result)


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: str,
    user: dict = Depends(get_current_user),
):
    """Delete an AI provider and cascade-delete all its models."""
    deleted = await ai_service.delete_provider(provider_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")


@router.put("/providers/{provider_id}", response_model=AIProviderResponse)
async def update_provider(
    provider_id: str,
    body: AIProviderUpdate,
    user: dict = Depends(get_current_user),
):
    """Update an existing AI provider."""
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        result = await ai_service.update_provider(provider_id, data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    return AIProviderResponse(**result)


# ── Test Connection ──────────────────────────────────────────────


@router.post("/test-connection", response_model=TestConnectionResponse)
async def test_connection(
    body: TestConnectionRequest,
    user: dict = Depends(get_current_user),
):
    """Test connectivity to an LLM provider endpoint.

    Hits the provider's /models endpoint to verify:
    - Endpoint URL is reachable
    - API key (if required) is valid
    - Returns list of available model IDs
    """
    result = await ai_service.test_connection(
        provider_type=body.type,
        endpoint=body.endpoint,
        api_key=body.api_key,
    )
    return TestConnectionResponse(**result)


# ── Models ─────────────────────────────────────────────────────


@router.get("/providers/{provider_id}/models", response_model=AIModelListResponse)
async def list_models(
    provider_id: str,
    user: dict = Depends(get_current_user),
):
    """List all AI models for a specific provider."""
    models = await ai_service.list_models(provider_id)
    model_responses = [AIModelResponse(**m) for m in models]
    return AIModelListResponse(models=model_responses, count=len(model_responses))


@router.post("/providers/{provider_id}/models", response_model=AIModelResponse, status_code=201)
async def create_model(
    provider_id: str,
    body: AIModelCreate,
    user: dict = Depends(get_current_user),
):
    """Create a new AI model under a provider."""
    # Ensure provider_id in path matches body (use path value)
    data = body.model_dump()
    data["provider_id"] = provider_id
    try:
        result = await ai_service.create_model(data, user["username"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return AIModelResponse(**result)


@router.delete("/models/{model_id}", status_code=204)
async def delete_model(
    model_id: str,
    user: dict = Depends(get_current_user),
):
    """Delete an AI model by ID."""
    deleted = await ai_service.delete_model(model_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")


@router.put("/models/{model_id}", response_model=AIModelResponse)
async def update_model(
    model_id: str,
    body: AIModelUpdate,
    user: dict = Depends(get_current_user),
):
    """Update an existing AI model."""
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        result = await ai_service.update_model(model_id, data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    return AIModelResponse(**result)
