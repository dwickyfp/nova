"""LLM provider client for the assistant (E4a).

The assistant **reuses** ``NOVA_SYSTEM.CONFIG_AI_PROVIDERS`` via
``ai_service`` (``app/modules/ai_ml/service.py``); it does not add a second
provider table or a second credential to manage. That was decision E4a.

Credential handling, stated because it is the whole risk surface:

* The provider API key is read with ``AIService.get_provider_api_key`` and held
  only for the duration of one outbound HTTP call. It is never logged, returned
  to a client, or placed on a thread/event.
* ``list_providers`` / ``get_provider`` already mask the key; this module must
  not bypass that for anything a client can see.
* A failure from the provider is surfaced as a redacted, generic message. The
  raw request body (which carries the user's schema context) is never echoed.
* The stored ``endpoint`` is run through ``app.common.ssrf_guard`` before every
  call (and at write time in ``ai_ml.router``), and the request itself uses the
  guard's transport so each redirect hop is re-checked (NOVA-119). The guard is
  the single home for URL validation — this module does not fork it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.common.ssrf_guard import (
    BlockedEndpointError,
    guarded_async_client,
    resolve_and_validate_url,
)
from app.core.exceptions import NovaException
from app.modules.ai_ml.service import ai_service

logger = logging.getLogger(__name__)


class AssistantProviderError(NovaException):
    """The provider could not be reached or refused the request.

    ``message`` is safe to surface: it carries a status code and a generic
    reason, never the API key, the request body, or the full response body.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=502)


@dataclass(frozen=True)
class ProviderConfig:
    """Everything needed for one outbound call, resolved fresh per request."""

    provider_id: str
    model: str
    endpoint: str
    api_key: str


class AssistantProviderClient:
    """Calls an OpenAI-compatible ``/chat/completions`` endpoint.

    Nova's AI SQL functions already assume this shape (see
    ``llm_functions.service`` endpoint composition), so the assistant follows
    the same convention rather than inventing a provider abstraction.
    """

    def __init__(self, timeout_seconds: float = 60.0) -> None:
        self._timeout = timeout_seconds

    @staticmethod
    def _chat_endpoint(endpoint: str) -> str:
        """Normalise a configured endpoint to its chat-completions path.

        Mirrors the composition in ``llm_functions.service`` so a provider
        configured for AI SQL functions also works for the assistant.
        """
        endpoint = endpoint.rstrip("/")
        if endpoint.endswith("/chat/completions"):
            return endpoint
        if endpoint.endswith("/v1"):
            return f"{endpoint}/chat/completions"
        return f"{endpoint}/v1/chat/completions"

    async def resolve(self) -> ProviderConfig:
        """Pick the active provider/model pair.

        v1 uses the first active provider that has an API key. Model selection
        is a follow-on concern (the spec does not require per-thread model
        choice); a provider whose key is unreadable is skipped rather than
        crashing the turn.
        """
        providers = await ai_service.list_providers()
        for provider in providers:
            if not provider.get("is_active", True) or not provider.get("has_api_key"):
                continue
            api_key = await ai_service.get_provider_api_key(provider["id"])
            if not api_key:
                continue
            model = await self._default_model(provider["id"])
            return ProviderConfig(
                provider_id=provider["id"],
                model=model,
                endpoint=self._chat_endpoint(provider["endpoint"]),
                api_key=api_key,
            )
        raise AssistantProviderError(
            "No active AI provider with an API key is configured. "
            "Add one under AI Providers before using the assistant."
        )

    async def _default_model(self, provider_id: str) -> str:
        """First active model for the provider, else a conservative default.

        The default name is a fallback only; a provider that has registered
        models should always resolve one, and the provider itself rejects an
        unknown model with a clear error if the fallback is wrong.
        """
        models = await ai_service.list_models(provider_id)
        for model in models:
            if model.get("is_active", True):
                return model["name"]
        return "gpt-4o-mini"

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        provider: ProviderConfig | None = None,
    ) -> dict[str, Any]:
        """One chat-completions call. Returns the raw assistant message dict.

        ``messages`` must already contain only content that is safe to send:
        redacted SQL, no credentials. This method does not redact — the caller
        owns context construction.
        """
        config = provider or await self.resolve()
        body: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools

        # The endpoint is a stored config value, not a literal the caller
        # controls, so it is validated here before the request as well as at
        # write time. The guard's transport re-checks every redirect hop, so a
        # provider that redirects to a private address is refused too. Same seam
        # as ``ai_ml.service.test_connection`` (NOVA-107/NOVA-119).
        try:
            resolve_and_validate_url(config.endpoint)
        except BlockedEndpointError:
            logger.warning(
                "Blocked assistant call to a non-public provider endpoint (provider=%s)",
                config.provider_id,
            )
            raise AssistantProviderError(
                "AI provider endpoint is not allowed: it must be a public http(s) URL"
            ) from None

        try:
            async with guarded_async_client(timeout=self._timeout) as client:
                response = await client.post(
                    config.endpoint,
                    headers={
                        "Authorization": f"Bearer {config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
        except BlockedEndpointError:
            # A redirect hop was refused by the guarded transport.
            logger.warning(
                "Blocked assistant call redirect to a non-public address (provider=%s)",
                config.provider_id,
            )
            raise AssistantProviderError(
                "AI provider endpoint is not allowed: it must be a public http(s) URL"
            ) from None
        except httpx.HTTPError as exc:
            # ``str(exc)`` can carry the URL, never the key or the body.
            raise AssistantProviderError(
                f"AI provider request failed: {type(exc).__name__}"
            ) from exc

        if response.status_code >= 400:
            # The status is safe; the body may echo the request, so it is not
            # put in the message (it goes to the debug log only, key-free).
            logger.warning(
                "Assistant provider call returned HTTP %s (provider=%s)",
                response.status_code,
                config.provider_id,
            )
            raise AssistantProviderError(
                f"AI provider returned HTTP {response.status_code}"
            )

        try:
            payload = response.json()
            return payload["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AssistantProviderError(
                "AI provider returned an unexpected response shape"
            ) from exc


assistant_provider = AssistantProviderClient()
