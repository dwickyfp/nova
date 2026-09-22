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

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
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
from app.modules.assistant.provider_capabilities import (
    CONSERVATIVE_OPENAI_COMPATIBLE,
    ProviderCapabilities,
)
from app.modules.assistant.streaming import StreamAccumulator, parse_sse_data_line

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


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
    capabilities: ProviderCapabilities = CONSERVATIVE_OPENAI_COMPATIBLE


class AssistantProviderClient:
    """Calls an OpenAI-compatible ``/chat/completions`` endpoint.

    Nova's AI SQL functions already assume this shape (see
    ``llm_functions.service`` endpoint composition), so the assistant follows
    the same convention rather than inventing a provider abstraction.
    """

    def __init__(
        self,
        timeout_seconds: float = 60.0,
        *,
        max_attempts: int = 3,
        retry_base_seconds: float = 0.25,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_attempts = max(1, max_attempts)
        self._retry_base_seconds = max(0.0, retry_base_seconds)

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

    async def resolve(
        self, *, provider_id: str | None = None, model: str | None = None
    ) -> ProviderConfig:
        """Pick the active provider/model pair.

        Defaults to the first active provider that has an API key. A caller may
        pin ``provider_id`` and/or ``model`` for one turn (the panel's model
        selector); a provider whose key is unreadable is skipped rather than
        crashing the turn.

        ``model`` is accepted only when it is a registered, active model of the
        chosen provider. An unknown name is rejected here rather than sent
        upstream, so a stale selection fails with a clear message instead of a
        provider-side error.
        """
        providers = await ai_service.list_providers()
        for provider in providers:
            if not provider.get("is_active", True) or not provider.get("has_api_key"):
                continue
            if provider_id and provider["id"] != provider_id:
                continue
            api_key = await ai_service.get_provider_api_key(provider["id"])
            if not api_key:
                continue
            resolved_model = await self._resolve_model(provider["id"], model)
            provider_params = provider.get("default_params") or {}
            capabilities = ProviderCapabilities.from_mapping(
                provider_params.get("capabilities")
                if isinstance(provider_params, dict)
                else None,
                base=CONSERVATIVE_OPENAI_COMPATIBLE,
            )
            for model_record in await ai_service.list_models(provider["id"]):
                if model_record.get("name") != resolved_model:
                    continue
                model_params = model_record.get("default_params") or {}
                capabilities = ProviderCapabilities.from_mapping(
                    model_params.get("capabilities")
                    if isinstance(model_params, dict)
                    else None,
                    base=capabilities,
                )
                max_tokens = model_record.get("max_tokens")
                if isinstance(max_tokens, int) and max_tokens > 0:
                    capabilities = ProviderCapabilities.from_mapping(
                        {"context_window": max_tokens}, base=capabilities
                    )
                break
            return ProviderConfig(
                provider_id=provider["id"],
                model=resolved_model,
                endpoint=self._chat_endpoint(provider["endpoint"]),
                api_key=api_key,
                capabilities=capabilities,
            )
        if provider_id:
            raise AssistantProviderError(
                "The selected AI provider is not available. "
                "Pick another model or provider in the assistant panel."
            )
        raise AssistantProviderError(
            "No active AI provider with an API key is configured. "
            "Add one under AI Providers before using the assistant."
        )

    async def _resolve_model(self, provider_id: str, requested: str | None) -> str:
        """First active model, or the caller's choice when it is registered.

        A provider that has no registered models falls back to a conservative
        default name; the provider itself then rejects it with a clear error.
        """
        models = await ai_service.list_models(provider_id)
        active = [m["name"] for m in models if m.get("is_active", True)]
        if requested:
            if requested not in active:
                raise AssistantProviderError(
                    "The selected model is not available for this provider. "
                    "Pick another model in the assistant panel."
                )
            return requested
        if active:
            return active[0]
        return "gpt-4o-mini"

    @staticmethod
    def _request_body(
        config: ProviderConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"model": config.model, "messages": messages}
        if tools and config.capabilities.supports_tools:
            if config.capabilities.supports_strict_tool_schema:
                tools = [
                    {
                        **tool,
                        "function": {**tool.get("function", {}), "strict": True},
                    }
                    for tool in tools
                ]
            body["tools"] = tools
            if tool_choice is not None and config.capabilities.supports_tool_choice:
                body["tool_choice"] = tool_choice
            if not config.capabilities.supports_parallel_tool_calls:
                body["parallel_tool_calls"] = False
        if response_format is not None and config.capabilities.supports_json_schema:
            body["response_format"] = response_format
        return body

    @staticmethod
    def _headers(config: ProviderConfig) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _endpoint_error(exc: BlockedEndpointError) -> AssistantProviderError:
        if exc.retryable:
            return AssistantProviderError(
                "AI provider hostname could not be resolved. Check DNS and retry."
            )
        return AssistantProviderError(
            "AI provider endpoint is not allowed: it must be a public http(s) URL"
        )

    async def _validate_endpoint(self, config: ProviderConfig) -> None:
        # The endpoint is a stored config value, not a literal the caller
        # controls, so it is validated here before the request as well as at
        # write time. The guard's transport re-checks every redirect hop, so a
        # provider that redirects to a private address is refused too. Same seam
        # as ``ai_ml.service.test_connection`` (NOVA-107/NOVA-119).
        for attempt in range(self._max_attempts):
            try:
                resolve_and_validate_url(config.endpoint)
                return
            except BlockedEndpointError as exc:
                if exc.retryable and attempt + 1 < self._max_attempts:
                    logger.info(
                        "Retrying assistant provider DNS resolution (provider=%s attempt=%s)",
                        config.provider_id,
                        attempt + 2,
                    )
                    await self._backoff(attempt)
                    continue
                if exc.retryable:
                    logger.warning(
                        "Assistant provider hostname could not be resolved (provider=%s)",
                        config.provider_id,
                    )
                else:
                    logger.warning(
                        "Blocked assistant call to a non-public provider endpoint (provider=%s)",
                        config.provider_id,
                    )
                raise self._endpoint_error(exc) from None

    def _raise_for_status(self, response: httpx.Response, config: ProviderConfig) -> None:
        if response.status_code >= 400:
            # The status is safe; the body may echo the request, so it is not
            # put in the message (it goes to the debug log only, key-free).
            logger.warning(
                "Assistant provider call returned HTTP %s (provider=%s)",
                response.status_code,
                config.provider_id,
            )
            raise AssistantProviderError(f"AI provider returned HTTP {response.status_code}")

    async def _backoff(self, attempt: int, response: httpx.Response | None = None) -> None:
        """Bound retry delay without exposing provider response content."""
        delay = min(self._retry_base_seconds * (2**attempt), 2.0)
        if response is not None:
            retry_after = response.headers.get("retry-after")
            if retry_after:
                with suppress(ValueError):
                    delay = min(max(float(retry_after), 0.0), 5.0)
        if delay:
            await asyncio.sleep(delay)

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        provider: ProviderConfig | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One non-streaming chat-completions call. Returns the assistant message.

        ``messages`` must already contain only content that is safe to send:
        redacted SQL, no credentials. This method does not redact — the caller
        owns context construction.
        """
        config = provider or await self.resolve()
        body = self._request_body(
            config,
            messages,
            tools,
            tool_choice=tool_choice,
            response_format=response_format,
        )
        await self._validate_endpoint(config)

        response: httpx.Response | None = None
        for attempt in range(self._max_attempts):
            try:
                async with guarded_async_client(timeout=self._timeout) as client:
                    response = await client.post(
                        config.endpoint, headers=self._headers(config), json=body
                    )
            except BlockedEndpointError as exc:
                # A redirect hop was refused by the guarded transport.
                if exc.retryable and attempt + 1 < self._max_attempts:
                    await self._backoff(attempt)
                    continue
                logger.warning(
                    "Blocked assistant call redirect or resolution (provider=%s)",
                    config.provider_id,
                )
                raise self._endpoint_error(exc) from None
            except httpx.HTTPError as exc:
                if attempt + 1 < self._max_attempts:
                    logger.info(
                        "Retrying assistant provider transport failure (provider=%s attempt=%s)",
                        config.provider_id,
                        attempt + 2,
                    )
                    await self._backoff(attempt)
                    continue
                # ``str(exc)`` can carry the URL, never the key or the body.
                raise AssistantProviderError(
                    f"AI provider request failed: {type(exc).__name__}"
                ) from exc

            if response.status_code in _RETRYABLE_STATUS_CODES and attempt + 1 < self._max_attempts:
                logger.info(
                    "Retrying assistant provider HTTP %s (provider=%s attempt=%s)",
                    response.status_code,
                    config.provider_id,
                    attempt + 2,
                )
                await self._backoff(attempt, response)
                continue
            self._raise_for_status(response, config)
            break

        if response is None:  # Defensive; the loop either assigns or raises.
            raise AssistantProviderError("AI provider request failed")

        try:
            payload = response.json()
            message = dict(payload["choices"][0]["message"])
            # Nested model calls (for example semantic SQL generation) use
            # ``complete`` rather than the streaming accumulator. Preserve
            # usage on the returned message so the enclosing agent run can
            # account for every provider call, not only the final response.
            if isinstance(payload.get("usage"), dict):
                message["usage"] = payload["usage"]
            return message
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AssistantProviderError(
                "AI provider returned an unexpected response shape"
            ) from exc

    async def stream(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        provider: ProviderConfig | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """One streaming chat-completions call.

        Yields ``("delta", text)`` for each content fragment as it arrives, then
        exactly one ``("message", dict)`` with the assembled assistant message
        (the same shape ``complete`` returns). Tool calls are accumulated from
        their fragmented deltas and only surfaced in that final message, because
        a tool call is not actionable until its arguments are complete.

        Raises ``AssistantProviderError`` for any transport or status failure;
        the caller maps that to an ``error`` frame.
        """
        config = provider or await self.resolve()
        body = self._request_body(config, messages, tools, tool_choice=tool_choice)
        body["stream"] = True
        # Ask the provider to report token usage on the final chunk. Without
        # this, OpenAI-compatible streamers omit `usage` entirely, and Studio
        # Observability has nothing to total. Providers that ignore the option
        # simply send no usage, which the accumulator tolerates.
        body["stream_options"] = {"include_usage": True}
        await self._validate_endpoint(config)

        for attempt in range(self._max_attempts):
            accumulator = StreamAccumulator()
            emitted_delta = False
            response: httpx.Response | None = None
            try:
                async with (
                    guarded_async_client(timeout=self._timeout) as client,
                    client.stream(
                        "POST",
                        config.endpoint,
                        headers=self._headers(config),
                        json=body,
                    ) as response,
                ):
                    if (
                        response.status_code in _RETRYABLE_STATUS_CODES
                        and attempt + 1 < self._max_attempts
                    ):
                        await self._backoff(attempt, response)
                        continue
                    self._raise_for_status(response, config)
                    async for line in response.aiter_lines():
                        payload = parse_sse_data_line(line)
                        if payload is None:
                            continue
                        text = accumulator.feed(payload)
                        if text:
                            emitted_delta = True
                            yield ("delta", text)
            except BlockedEndpointError as exc:
                if exc.retryable and not emitted_delta and attempt + 1 < self._max_attempts:
                    await self._backoff(attempt, response)
                    continue
                logger.warning(
                    "Blocked assistant call redirect or resolution (provider=%s)",
                    config.provider_id,
                )
                raise self._endpoint_error(exc) from None
            except httpx.HTTPError as exc:
                # Retrying after a visible delta would duplicate user-visible
                # text. Fail that run and let the caller persist the partial
                # trace instead. Handshake/transport failures are safe to retry.
                if not emitted_delta and attempt + 1 < self._max_attempts:
                    await self._backoff(attempt, response)
                    continue
                raise AssistantProviderError(
                    f"AI provider request failed: {type(exc).__name__}"
                ) from exc

            yield ("message", accumulator.message())
            return


assistant_provider = AssistantProviderClient()
