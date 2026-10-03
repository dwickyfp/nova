"""Opt-in live provider guard with a conservative reservation before every call."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from unittest.mock import patch

from app.modules.assistant.provider import AssistantProviderClient


def transport_fingerprint(config) -> str:
    from urllib.parse import urlsplit

    endpoint = urlsplit(AssistantProviderClient._chat_endpoint(config.endpoint))
    public = [endpoint.scheme, endpoint.hostname, endpoint.port, endpoint.path]
    return hashlib.sha256(json.dumps(public, separators=(",", ":")).encode()).hexdigest()


@dataclass
class LiveBudget:
    maximum_dollars: float
    input_dollars_per_million: float
    output_dollars_per_million: float
    maximum_calls: int = 100
    output_tokens_per_call: int = 2048
    reserved_dollars: float = 0
    calls: int = 0
    maximum_input_tokens: int = 32000
    provider_id: str | None = None
    model: str | None = None
    transport_fingerprint: str | None = None

    def reserve(self, messages: list, tools: list | None, attempts: int) -> None:
        if (
            min(
                self.maximum_dollars,
                self.input_dollars_per_million,
                self.output_dollars_per_million,
            )
            <= 0
        ):
            raise ValueError(
                "Live runs require explicit positive cost limits and current provider rates"
            )
        if self.calls + attempts > self.maximum_calls:
            raise RuntimeError("Live provider call ceiling reached")
        # Byte length bounds the visible input tokens conservatively. Reserve a
        # separate framing allowance and every possible transport retry.
        inputs = (
            len(json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False).encode())
            + 4096
        )
        if inputs > self.maximum_input_tokens:
            raise RuntimeError("Live input exceeds the configured token ceiling")
        cost = (
            attempts
            * (
                inputs * self.input_dollars_per_million
                + self.output_tokens_per_call * self.output_dollars_per_million
            )
            / 1_000_000
        )
        if self.reserved_dollars + cost > self.maximum_dollars:
            raise RuntimeError("Live provider cost reservation ceiling reached")
        self.calls += attempts
        self.reserved_dollars += cost

    @contextmanager
    def guard(self):
        from app.modules.ai_ml.embeddings import EmbeddingError, EmbeddingService

        complete, stream, request_body = (
            AssistantProviderClient.complete,
            AssistantProviderClient.stream,
            AssistantProviderClient._request_body,
        )
        budget = self

        async def pin_provider(client, kwargs):
            config = kwargs.get("provider") or await client.resolve()
            if (
                (budget.provider_id and config.provider_id != budget.provider_id)
                or (budget.model and config.model != budget.model)
                or (
                    budget.transport_fingerprint
                    and transport_fingerprint(config) != budget.transport_fingerprint
                )
            ):
                raise RuntimeError("Provider changed after freezing")
            kwargs["provider"] = config

        async def no_embeddings(*_args, **_kwargs):
            raise EmbeddingError("This experiment pins lexical memory retrieval")

        async def bounded_complete(client, *, messages, tools=None, **kwargs):
            await pin_provider(client, kwargs)
            budget.reserve(messages, tools, client._max_attempts)
            return await complete(client, messages=messages, tools=tools, **kwargs)

        async def bounded_stream(client, *, messages, tools=None, **kwargs):
            await pin_provider(client, kwargs)
            budget.reserve(messages, tools, client._max_attempts)
            async for event in stream(client, messages=messages, tools=tools, **kwargs):
                yield event

        def capped_body(config, messages, tools, **kwargs):
            body = request_body(config, messages, tools, **kwargs)
            body["max_tokens"] = budget.output_tokens_per_call
            return body

        with (
            patch.object(AssistantProviderClient, "complete", bounded_complete),
            patch.object(AssistantProviderClient, "stream", bounded_stream),
            patch.object(AssistantProviderClient, "_request_body", staticmethod(capped_body)),
            patch.object(EmbeddingService, "embed_batch", no_embeddings),
        ):
            yield self
