"""AI Provider Management API router — provider and model CRUD endpoints.

Endpoints under /api/v1/ai:
  GET    /providers                  → list all providers
  POST   /providers                 → create provider (admin)
  DELETE /providers/{id}            → delete provider (admin, cascades models)
  PUT    /providers/{id}            → update provider (admin)
  GET    /providers/{id}/models     → list models for provider
  POST   /providers/{id}/models     → create model
  DELETE /models/{id}               → delete model
  PUT    /providers/{id}/api-key    → update provider API key (admin)
  GET    /web-search                → whether the Tavily web-search key is set
  PUT    /web-search/api-key        → store the Tavily API key, write-only (admin)
  DELETE /web-search/api-key        → remove the Tavily API key (admin)

Provider records are **system configuration**: the stored ``endpoint`` is later
called server-side by ``test_connection`` and by the assistant. The write routes
are therefore gated to the repo's existing admin role set, and the endpoint is
validated through ``app.common.ssrf_guard`` at store time so a private/loopback/
link-local target cannot be persisted in the first place (NOVA-119). The
per-request guard remains the authoritative check.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.common.audit import write_audit_log
from app.common.ssrf_guard import BlockedEndpointError, resolve_and_validate_url
from app.core.deps import get_current_user, require_role
from app.modules.ai_ml.default_model import (
    DefaultModelSettings,
    read_default_model,
    save_default_model,
)
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
from app.modules.ai_ml.web_search_settings import (
    TavilyKeyRequest,
    WebSearchStatus,
    clean_tavily_key,
    clear_tavily_api_key,
    save_tavily_api_key,
    web_search_status,
)
from app.modules.users.router import ADMIN_ROLES

router = APIRouter()

# Provider config is system config. Reused, not re-declared, from the repo's
# admin role set (same list ``/users``, monitoring and task orchestration use).
require_admin = Depends(require_role(*ADMIN_ROLES))

# Read routes still require only authentication. Built once so routes use a
# module-level dependency instead of calling ``Depends(...)`` in argument
# defaults (ruff B008), matching the admin surface.
require_user = Depends(get_current_user)


def _validate_provider_endpoint(endpoint: str) -> None:
    """Reject a link-local, metadata or malformed endpoint before it is stored.

    Provider writes are admin-only, so a model served on localhost or the
    private network is accepted. The message is fixed and carries no resolved
    address, matching the wording in ``ai_ml.service.test_connection``
    (NOVA-107).
    """
    try:
        resolve_and_validate_url(endpoint, allow_private_network=True)
    except BlockedEndpointError:
        raise HTTPException(
            status_code=400,
            detail=(
                "Endpoint is not allowed: use an http(s) URL on the internet "
                "or your local network"
            ),
        ) from None


# ── Providers ──────────────────────────────────────────────────


@router.get("/default-model", response_model=DefaultModelSettings)
async def get_default_model(user: dict = require_user):
    return await read_default_model()


@router.put("/default-model", response_model=DefaultModelSettings)
async def update_default_model(body: DefaultModelSettings, user: dict = require_admin):
    try:
        result = await save_default_model(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    await write_audit_log(
        event_type="AI_PROVIDER", user_name=user["username"], action="UPDATE",
        object_type="DEFAULT_MODEL", object_name=body.model_id or "automatic", status="SUCCESS",
        session_id=user.get("session_id"), active_role=user.get("active_role"),
    )
    return result


@router.get("/web-search", response_model=WebSearchStatus)
async def get_web_search(user: dict = require_user):
    """Whether Studio web search has a Tavily key. The key itself is never returned."""
    return await web_search_status()


@router.put("/web-search/api-key", response_model=WebSearchStatus)
async def update_web_search_key(body: TavilyKeyRequest, user: dict = require_admin):
    """Store the Tavily API key encrypted. Write-only: the response carries status only."""
    from app.common.crypto import EncryptionError

    api_key = clean_tavily_key(body.api_key)
    if api_key is None:
        raise HTTPException(status_code=400, detail="Enter a valid Tavily API key.")
    try:
        result = await save_tavily_api_key(api_key)
    except EncryptionError:
        raise HTTPException(
            status_code=500, detail="The key could not be stored securely."
        ) from None
    await write_audit_log(
        event_type="AI_PROVIDER", user_name=user["username"], action="UPDATE",
        object_type="WEB_SEARCH_KEY", object_name="tavily", status="SUCCESS",
        session_id=user.get("session_id"), active_role=user.get("active_role"),
    )
    return result


@router.delete("/web-search/api-key", response_model=WebSearchStatus)
async def delete_web_search_key(user: dict = require_admin):
    """Remove the Tavily API key; agents then report that web search is not configured."""
    result = await clear_tavily_api_key()
    await write_audit_log(
        event_type="AI_PROVIDER", user_name=user["username"], action="DELETE",
        object_type="WEB_SEARCH_KEY", object_name="tavily", status="SUCCESS",
        session_id=user.get("session_id"), active_role=user.get("active_role"),
    )
    return result


@router.get("/providers", response_model=AIProviderListResponse)
async def list_providers(
    user: dict = require_user,
):
    """List all registered AI providers."""
    providers = await ai_service.list_providers()
    provider_responses = [AIProviderResponse(**p) for p in providers]
    return AIProviderListResponse(providers=provider_responses, count=len(provider_responses))


@router.post("/providers", response_model=AIProviderResponse, status_code=201)
async def create_provider(
    body: AIProviderCreate,
    user: dict = require_admin,
):
    """Create a new AI provider. Admin-only: provider config is system config."""
    _validate_provider_endpoint(body.endpoint)
    try:
        result = await ai_service.create_provider(body.model_dump(), user["username"])
        if result is None:
            raise RuntimeError("Created AI provider is unavailable")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await write_audit_log(
        event_type="AI_PROVIDER", user_name=user["username"], action="CREATE",
        object_type="AI_PROVIDER", object_name=result["id"], status="SUCCESS",
        session_id=user.get("session_id"), active_role=user.get("active_role"),
    )
    return AIProviderResponse(**result)


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: str,
    user: dict = require_admin,
):
    """Delete an AI provider and cascade-delete all its models. Admin-only."""
    deleted = await ai_service.delete_provider(provider_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    await write_audit_log(
        event_type="AI_PROVIDER", user_name=user["username"], action="DELETE",
        object_type="AI_PROVIDER", object_name=provider_id, status="SUCCESS",
        session_id=user.get("session_id"), active_role=user.get("active_role"),
    )


@router.put("/providers/{provider_id}", response_model=AIProviderResponse)
async def update_provider(
    provider_id: str,
    body: AIProviderUpdate,
    user: dict = require_admin,
):
    """Update an existing AI provider. Admin-only: provider config is system config."""
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    if data.get("endpoint") is not None:
        _validate_provider_endpoint(data["endpoint"])
    try:
        result = await ai_service.update_provider(provider_id, data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not result:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    await write_audit_log(
        event_type="AI_PROVIDER", user_name=user["username"], action="UPDATE",
        object_type="AI_PROVIDER", object_name=provider_id, status="SUCCESS",
        session_id=user.get("session_id"), active_role=user.get("active_role"),
    )
    return AIProviderResponse(**result)


# ── Test Connection ──────────────────────────────────────────────


@router.post("/test-connection", response_model=TestConnectionResponse)
async def test_connection(
    body: TestConnectionRequest,
    user: dict = require_user,
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
        # Only administrators can save a provider, so only they may probe the
        # local network; everyone else keeps the public-only check.
        allow_private_network=any(role in user["roles"] for role in ADMIN_ROLES),
    )
    return TestConnectionResponse(**result)


# ── Models ─────────────────────────────────────────────────────


@router.get("/providers/{provider_id}/models", response_model=AIModelListResponse)
async def list_models(
    provider_id: str,
    user: dict = require_user,
):
    """List all AI models for a specific provider."""
    models = await ai_service.list_models(provider_id)
    model_responses = [AIModelResponse(**m) for m in models]
    return AIModelListResponse(models=model_responses, count=len(model_responses))


@router.post("/providers/{provider_id}/models", response_model=AIModelResponse, status_code=201)
async def create_model(
    provider_id: str,
    body: AIModelCreate,
    user: dict = require_admin,
):
    """Create a new AI model under a provider."""
    # Ensure provider_id in path matches body (use path value)
    data = body.model_dump()
    data["provider_id"] = provider_id
    try:
        result = await ai_service.create_model(data, user["username"])
        if result is None:
            raise RuntimeError("Created AI model is unavailable")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await write_audit_log(
        event_type="AI_MODEL",
        user_name=user["username"],
        action="CREATE",
        object_type="AI_MODEL",
        object_name=result["id"],
        status="SUCCESS",
        session_id=user.get("session_id"),
        active_role=user.get("active_role"),
    )
    return AIModelResponse(**result)


@router.delete("/models/{model_id}", status_code=204)
async def delete_model(
    model_id: str,
    user: dict = require_admin,
):
    """Delete an AI model by ID."""
    deleted = await ai_service.delete_model(model_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    await write_audit_log(
        event_type="AI_MODEL",
        user_name=user["username"],
        action="DELETE",
        object_type="AI_MODEL",
        object_name=model_id,
        status="SUCCESS",
        session_id=user.get("session_id"),
        active_role=user.get("active_role"),
    )


@router.put("/models/{model_id}", response_model=AIModelResponse)
async def update_model(
    model_id: str,
    body: AIModelUpdate,
    user: dict = require_admin,
):
    """Update an existing AI model."""
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        result = await ai_service.update_model(model_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not result:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    await write_audit_log(
        event_type="AI_MODEL",
        user_name=user["username"],
        action="UPDATE",
        object_type="AI_MODEL",
        object_name=model_id,
        status="SUCCESS",
        session_id=user.get("session_id"),
        active_role=user.get("active_role"),
    )
    return AIModelResponse(**result)


# ── API Key Update ─────────────────────────────────────────────


class UpdateAPIKeyRequest(BaseModel):
    """Update API key for a provider."""
    api_key: str = Field(..., min_length=1)


@router.put("/providers/{provider_id}/api-key")
async def update_api_key(
    provider_id: str,
    req: UpdateAPIKeyRequest,
    user: dict = require_admin,
):
    """Update only the API key for a provider. Key is encrypted before storage.

    Admin-only: it rotates a system credential.
    """
    from app.common.crypto import encrypt
    encrypted_key = encrypt(req.api_key)
    
    conn = await ai_service._connect()
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE NOVA_SYSTEM.CONFIG_AI_PROVIDERS SET api_key = %s WHERE id = %s",
                (encrypted_key, provider_id),
            )
    finally:
        conn.close()
    
    return {"success": True, "message": "API key updated and encrypted"}
