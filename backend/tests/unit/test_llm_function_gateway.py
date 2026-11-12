"""The internal LLM gateway keeps provider keys out of the engine.

``AI_*`` function bodies used to embed the decrypted provider key, readable by
anyone who could see the function. They now carry a token that only names an
alias; the gateway holds the key, picks the model, and counts the tokens.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.metadata_db import MetadataOutcomeUnknown
from app.modules.llm_functions import gateway
from app.modules.llm_functions.gateway import (
    GatewayRefusal,
    LLMGateway,
    UsageMeter,
    alias_from_token,
    alias_token,
    provider_endpoint,
)

PROVIDER_KEY = "sk-provider-secret-do-not-leak"
ALIAS = {
    "id": "alias-1",
    "alias_name": "default-complete",
    "function_type": "complete",
    "provider_id": "provider-1",
    "model_id": None,
    "model_name": "provider-model",
    "is_active": True,
}
PROVIDER = {
    "id": "provider-1",
    "name": "fixture",
    "type": "openai",
    "is_active": True,
    "endpoint": "https://llm.example.com/v1",
}
REPLY = {
    "choices": [{"message": {"role": "assistant", "content": "pong"}}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
}
REQUEST = {
    "model": "anything",
    "messages": [{"role": "user", "content": "ping"}],
    "temperature": 0.0,
    "max_tokens": 16,
    "top_p": 1.0,
    "stream": True,
}


class _Provider:
    """Stands in for the guarded HTTP client and records what it was sent."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.response: httpx.Response | Exception = httpx.Response(200, json=REPLY)

    def client(self, *, timeout, allow_private_network=False, loopback_host=None):
        self.timeout = timeout
        self.allow_private_network = allow_private_network
        self.loopback_host = loopback_host
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *, headers, json):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture
def stack(monkeypatch):
    from app.modules.ai_ml.service import ai_service
    from app.modules.llm_functions.service import llm_function_service

    provider = _Provider()
    alias, upstream = dict(ALIAS), dict(PROVIDER)
    monkeypatch.setattr(llm_function_service, "get_alias", AsyncMock(side_effect=lambda _: alias))
    monkeypatch.setattr(ai_service, "get_provider", AsyncMock(side_effect=lambda _: upstream))
    monkeypatch.setattr(ai_service, "get_provider_api_key", AsyncMock(return_value=PROVIDER_KEY))
    monkeypatch.setattr(gateway, "guarded_async_client", provider.client)
    return LLMGateway(), provider, alias, upstream


# ── Tokens ──────────────────────────────────────────────────────────────────


def test_token_names_exactly_one_alias():
    token = alias_token("alias-1")

    assert alias_from_token(token) == "alias-1"
    assert PROVIDER_KEY not in token and settings.SECRET_KEY not in token


@pytest.mark.parametrize(
    "forged",
    [
        "",
        "nvf1.alias-1.",
        "nvf1.alias-2." + alias_token("alias-1").rsplit(".", 1)[1],
        alias_token("alias-1")[:-1] + "0",
        alias_token("alias-1").replace("nvf1", "nvf2"),
        PROVIDER_KEY,
    ],
)
def test_forged_or_reused_tokens_are_rejected(forged):
    assert alias_from_token(forged) is None


def test_rotating_the_signing_key_invalidates_every_token(monkeypatch):
    token = alias_token("alias-1")
    monkeypatch.setattr(settings, "SECRET_KEY", "a-different-signing-key")

    assert alias_from_token(token) is None


def test_alias_ids_containing_dots_round_trip():
    assert alias_from_token(alias_token("team.alias.v2")) == "team.alias.v2"


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("https://x/v1", "https://x/v1/chat/completions"),
        ("https://x/v1/", "https://x/v1/chat/completions"),
        ("https://x", "https://x/v1/chat/completions"),
        ("https://x/v1/chat/completions", "https://x/v1/chat/completions"),
    ],
)
def test_provider_endpoint_is_completed(base, expected):
    assert provider_endpoint(base) == expected


# ── Forwarding ──────────────────────────────────────────────────────────────


async def test_request_is_forwarded_with_the_provider_key_and_the_alias_model(stack):
    llm, provider, _, _ = stack

    status, content = await llm.complete(alias_token("alias-1"), REQUEST)

    assert (status, content) == (200, REPLY)
    call = provider.calls[0]
    assert call["url"] == "https://llm.example.com/v1/chat/completions"
    assert call["headers"] == {"Authorization": f"Bearer {PROVIDER_KEY}"}
    # The alias chooses the model; the caller's model and extra fields are dropped.
    assert call["json"] == {
        "model": "provider-model",
        "messages": REQUEST["messages"],
        "temperature": 0.0,
        "max_tokens": 16,
        "top_p": 1.0,
    }
    assert provider.timeout == settings.LLM_GATEWAY_TIMEOUT_SECONDS


@pytest.mark.parametrize("token", ["", "garbage", PROVIDER_KEY])
async def test_bad_token_never_reaches_the_provider(stack, token):
    llm, provider, _, _ = stack

    with pytest.raises(GatewayRefusal) as refused:
        await llm.complete(token, REQUEST)

    assert refused.value.status_code == 401 and provider.calls == []


@pytest.mark.parametrize("body", [None, [], {}, {"messages": "ping"}])
async def test_malformed_request_is_refused(stack, body):
    llm, provider, _, _ = stack

    with pytest.raises(GatewayRefusal) as refused:
        await llm.complete(alias_token("alias-1"), body)

    assert refused.value.status_code == 400 and provider.calls == []


async def test_deleted_or_disabled_alias_stops_its_token(stack):
    llm, provider, alias, _ = stack
    alias["is_active"] = False

    with pytest.raises(GatewayRefusal) as refused:
        await llm.complete(alias_token("alias-1"), REQUEST)

    assert refused.value.status_code == 401 and provider.calls == []


@pytest.mark.parametrize(
    "change",
    [{"is_active": False}, {"endpoint": ""}],
)
async def test_unusable_provider_is_unavailable(stack, change):
    llm, provider, _, upstream = stack
    upstream.update(change)

    with pytest.raises(GatewayRefusal) as refused:
        await llm.complete(alias_token("alias-1"), REQUEST)

    assert refused.value.status_code == 503 and provider.calls == []


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (httpx.Response(401, json={"error": f"bad key {PROVIDER_KEY}"}), 502),
        (httpx.Response(429, json={"error": "slow down"}), 429),
        (httpx.Response(500, text=f"upstream echoed {PROVIDER_KEY}"), 502),
        (httpx.ConnectError(f"cannot reach host with {PROVIDER_KEY}"), 502),
        (httpx.ReadTimeout("slow"), 502),
        (httpx.Response(200, text="not json"), 502),
    ],
)
async def test_provider_failures_never_carry_the_key(stack, caplog, failure, status):
    llm, provider, _, _ = stack
    provider.response = failure

    with pytest.raises(GatewayRefusal) as refused:
        await llm.complete(alias_token("alias-1"), REQUEST)

    assert refused.value.status_code == status
    assert PROVIDER_KEY not in refused.value.message and PROVIDER_KEY not in caplog.text


async def test_blocked_endpoint_is_reported_as_a_provider_failure(stack):
    from app.common.ssrf_guard import BlockedEndpointError

    llm, provider, _, _ = stack
    provider.response = BlockedEndpointError("blocked")

    with pytest.raises(GatewayRefusal) as refused:
        await llm.complete(alias_token("alias-1"), REQUEST)

    assert refused.value.status_code == 502


async def test_alias_lookup_is_cached_and_can_be_dropped(stack):
    from app.modules.llm_functions.service import llm_function_service

    llm, _, _, _ = stack
    token = alias_token("alias-1")

    await llm.complete(token, REQUEST)
    await llm.complete(token, REQUEST)
    assert llm_function_service.get_alias.await_count == 1

    llm.forget("alias-1")
    await llm.complete(token, REQUEST)
    assert llm_function_service.get_alias.await_count == 2


async def test_forwarding_is_bounded(monkeypatch, stack):
    llm, provider, _, _ = stack
    monkeypatch.setattr(settings, "LLM_GATEWAY_MAX_CONCURRENCY", 2)
    running = peak = 0
    release = asyncio.Event()

    async def slow_post(url, *, headers, json):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await release.wait()
        running -= 1
        return httpx.Response(200, json=REPLY)

    provider.post = slow_post
    calls = [asyncio.create_task(llm.complete(alias_token("alias-1"), REQUEST)) for _ in range(6)]
    await asyncio.sleep(0.01)
    assert peak == 2
    release.set()
    await asyncio.gather(*calls)


# ── Usage ───────────────────────────────────────────────────────────────────


class _UsageStore:
    def __init__(self):
        self.rows = []
        self.fail = False
        self.unknown = False

    async def record(self, rows):
        if self.fail:
            raise ConnectionError("metadata unavailable")
        if self.unknown:
            raise MetadataOutcomeUnknown()
        self.rows.extend(rows)


@pytest.fixture
def usage_store(monkeypatch, fake_metadata_db):
    store = _UsageStore()

    def repository(connection, actor):
        assert actor == gateway.USAGE_WRITER
        assert fake_metadata_db.transactions[-1].actor == actor
        return store

    monkeypatch.setattr(gateway, "LLMUsageMetadataRepository", repository)
    return store


async def test_usage_is_summed_per_function_and_written_in_one_row(stack, usage_store):
    llm, provider, _, _ = stack
    token = alias_token("alias-1")

    for _ in range(3):
        await llm.complete(token, REQUEST)
    provider.response = httpx.Response(500, text="down")
    with pytest.raises(GatewayRefusal):
        await llm.complete(token, REQUEST)

    assert usage_store.rows == []
    assert await llm.meter.flush() == 2
    by_status = {row[3]: row for row in usage_store.rows}
    success = by_status["success"]
    assert success[:3] == ("AI_COMPLETE", "default-complete", "provider-model")
    assert success[4:8] == (3, 33, 12, 45)
    assert by_status["error"][4:8] == (1, 0, 0, 0)
    assert await llm.meter.flush() == 0


async def test_usage_written_never_contains_secrets(stack, usage_store):
    llm, _, _, _ = stack

    await llm.complete(alias_token("alias-1"), REQUEST)
    await llm.meter.flush()

    written = json.dumps(usage_store.rows, default=str)
    assert PROVIDER_KEY not in written and alias_token("alias-1") not in written


async def test_failed_flush_keeps_the_usage_for_the_next_one(stack, usage_store):
    llm, _, _, _ = stack
    await llm.complete(alias_token("alias-1"), REQUEST)

    usage_store.fail = True
    with pytest.raises(ConnectionError):
        await llm.meter.flush()
    usage_store.fail = False
    await llm.complete(alias_token("alias-1"), REQUEST)

    assert await llm.meter.flush() == 1
    assert usage_store.rows[0][4:8] == (2, 22, 8, 30)


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": "9"}, {"prompt_tokens": -1}, []])
async def test_missing_or_odd_usage_counts_the_request_without_tokens(usage_store, usage):
    meter = UsageMeter()
    resolved = gateway.Resolution("AI_COMPLETE", "a", "m", "https://x", "k")

    meter.record(resolved, "success", usage, 5)
    await meter.flush()

    assert usage_store.rows[0][4:9] == (1, 0, 0, 0, 5)


async def test_meter_loop_flushes_on_interval_and_at_stop(monkeypatch, usage_store):
    monkeypatch.setattr(settings, "LLM_USAGE_FLUSH_SECONDS", 0.01)
    meter = UsageMeter()
    resolved = gateway.Resolution("AI_COMPLETE", "a", "m", "https://x", "k")
    stop = asyncio.Event()
    loop = asyncio.create_task(meter.run(stop))

    meter.record(resolved, "success", REPLY["usage"], 1)
    await asyncio.sleep(0.05)
    assert len(usage_store.rows) == 1

    meter.record(resolved, "success", REPLY["usage"], 1)
    stop.set()
    await loop
    assert len(usage_store.rows) == 2


# ── Route ───────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch, stack):
    llm, provider, _, _ = stack
    monkeypatch.setattr(gateway, "llm_gateway", llm)
    app = FastAPI()
    app.include_router(gateway.router, prefix="/api/v1/internal/llm")
    return TestClient(app), provider


def test_route_answers_the_engine_in_the_openai_shape(client):
    http, _ = client

    response = http.post(
        gateway.GATEWAY_PATH,
        json=REQUEST,
        headers={"Authorization": f"Bearer {alias_token('alias-1')}"},
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "pong"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer nope"}],
)
def test_route_rejects_callers_without_a_valid_token(client, headers):
    http, provider = client

    response = http.post(gateway.GATEWAY_PATH, json=REQUEST, headers=headers)

    assert response.status_code == 401 and provider.calls == []
    assert response.json()["error"]["type"] == "nova_gateway_error"


def test_route_rejects_a_body_that_is_not_json(client):
    http, _ = client

    response = http.post(
        gateway.GATEWAY_PATH,
        content=b"not json",
        headers={"Authorization": f"Bearer {alias_token('alias-1')}"},
    )

    assert response.status_code == 400


def test_gateway_is_mounted_on_the_internal_prefix():
    from app.main import app

    # 401 from the gateway, not 404: the route exists on the application.
    assert TestClient(app).post(gateway.GATEWAY_PATH, json=REQUEST).status_code == 401


# Function registration runs in maintenance; rendering is deterministic.


def snapshot(**changes):
    return {
        "contract_version": 1,
        "function_type": "complete",
        "mode": "configured",
        "alias": {"id": "alias-1", "system_prompt": ""},
        "model": {"name": "provider-model"},
        "parameters": {"max_tokens": 64},
        **changes,
    }


def test_registered_body_carries_only_gateway_token(monkeypatch):
    from app.modules.llm_functions.service import render_function

    monkeypatch.setattr(settings, "LLM_GATEWAY_URL", "http://nova-query:8000/")
    rendered = render_function(
        snapshot(
            parameters={
                "max_tokens": 64,
                "timeout_ms": 20000,
                "api_key": PROVIDER_KEY,
                "endpoint": "https://attacker.example.com",
            }
        )
    )
    config = json.loads(rendered.body.split("ai_query(prompt, '", 1)[1].rsplit("')", 1)[0])
    assert config == {
        "max_tokens": 64,
        "timeout_ms": 20000,
        "model": "provider-model",
        "api_key": alias_token("alias-1"),
        "endpoint": "http://nova-query:8000/api/v1/internal/llm/chat/completions",
    }
    assert PROVIDER_KEY not in rendered.create_sql
    assert alias_token("alias-1") not in repr(rendered)


@pytest.mark.parametrize("mode", ["unconfigured", "disabled"])
def test_unavailable_definition_renders_a_non_invoking_placeholder(mode):
    from app.modules.llm_functions.service import render_function

    rendered = render_function(snapshot(mode=mode))
    assert "not configured" in rendered.body and "ai_query(" not in rendered.body


@pytest.mark.parametrize(
    "function_type",
    ["complete", "sentiment", "classify", "summarize", "extract", "translate", "filter"],
)
def test_full_stored_body_is_verified_through_novas_parser(function_type):
    from app.modules.llm_functions.service import function_body_matches, render_function

    rendered = render_function(snapshot(function_type=function_type))
    assert function_body_matches(rendered, rendered.body)
    assert function_body_matches(rendered, rendered.body.replace("ai_query(", "AI_QUERY ("))


@pytest.mark.parametrize(
    "change", ["token", "endpoint", "model", "prompt", "parameter", "placeholder", "suffix"]
)
def test_same_alias_markers_are_not_sufficient_proof(change):
    from app.modules.llm_functions.service import function_body_matches, render_function

    rendered = render_function(
        snapshot(alias={"id": "alias-1", "system_prompt": "original prompt"})
    )
    body = rendered.body
    if change == "token":
        body = body.replace(alias_token("alias-1"), PROVIDER_KEY)
    elif change == "endpoint":
        body = body.replace(gateway.gateway_endpoint(), "https://attacker.example.test")
    elif change == "model":
        body = body.replace("provider-model", "unapproved-model")
    elif change == "prompt":
        body = body.replace("original prompt", "different prompt")
    elif change == "parameter":
        body = body.replace('"max_tokens": 64', '"max_tokens": 128')
    elif change == "placeholder":
        body = "concat('ERROR: not configured', prompt)"
    else:
        body += " + 1"
    assert not function_body_matches(rendered, body)


def test_equivalent_identifier_quoting_and_json_layout_are_allowed():
    from app.modules.llm_functions.service import function_body_matches, render_function

    rendered = render_function(snapshot())
    body = rendered.body.replace("(prompt,", "(`prompt`,").replace('": ', '":')
    assert function_body_matches(rendered, body)


def test_malformed_or_oversized_engine_body_is_not_proof():
    from app.modules.llm_functions.service import function_body_matches, render_function

    rendered = render_function(snapshot())
    for body in ["not SQL )", "x" * 262145]:
        assert not function_body_matches(rendered, body)


async def test_unknown_usage_commit_is_never_replayed(stack, usage_store):
    llm, _, _, _ = stack
    await llm.complete(alias_token("alias-1"), REQUEST)
    usage_store.unknown = True
    with pytest.raises(MetadataOutcomeUnknown):
        await llm.meter.flush()
    usage_store.unknown = False
    assert await llm.meter.flush() == 0
    await llm.complete(alias_token("alias-1"), REQUEST)
    assert await llm.meter.flush() == 1
    assert usage_store.rows[0][4:8] == (1, 11, 4, 15)
