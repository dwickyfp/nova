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

    def client(self, *, timeout):
        self.timeout = timeout
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
    [{"is_active": False}, {"type": "decision"}, {"endpoint": ""}],
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


class _Connection:
    def __init__(self, fail: bool = False) -> None:
        self.rows: list[tuple] = []
        self.fail = fail

    def cursor(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def executemany(self, sql, rows):
        if self.fail:
            raise ConnectionError("engine unavailable")
        assert "USAGE_LLM_FUNCTIONS" in sql
        self.rows.extend(rows)


@pytest.fixture
def usage_store(monkeypatch):
    connection = _Connection()
    monkeypatch.setattr(gateway.db, "system_conn", lambda: connection)
    return connection


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
    by_status = {row[4]: row for row in usage_store.rows}
    success = by_status["success"]
    assert success[1:4] == ("AI_COMPLETE", "default-complete", "provider-model")
    assert success[5:9] == (3, 33, 12, 45)
    assert by_status["error"][5:9] == (1, 0, 0, 0)
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
    assert usage_store.rows[0][5:9] == (2, 22, 8, 30)


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": "9"}, {"prompt_tokens": -1}, []])
async def test_missing_or_odd_usage_counts_the_request_without_tokens(usage_store, usage):
    meter = UsageMeter()
    resolved = gateway.Resolution("AI_COMPLETE", "a", "m", "https://x", "k")

    meter.record(resolved, "success", usage, 5)
    await meter.flush()

    assert usage_store.rows[0][5:10] == (1, 0, 0, 0, 5)


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


# ── Function bodies ─────────────────────────────────────────────────────────


class _Engine:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def cursor(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, *args):
        self.statements.append(sql)

    def close(self):
        pass


async def test_registered_function_body_carries_the_token_not_the_provider_key(monkeypatch):
    from app.modules.llm_functions.service import LLMFunctionService

    monkeypatch.setattr(settings, "LLM_GATEWAY_URL", "http://nova-query:8000/")
    engine = _Engine()
    service = LLMFunctionService()
    monkeypatch.setattr(
        service,
        "_get_provider",
        AsyncMock(return_value={**PROVIDER, "api_key": f"enc:{PROVIDER_KEY}"}),
    )
    monkeypatch.setattr(service, "_connect", AsyncMock(return_value=engine))
    alias = {
        **ALIAS,
        "default_params": {
            "max_tokens": 64,
            "timeout_ms": 20000,
            "api_key": PROVIDER_KEY,
            "endpoint": "https://attacker.example.com",
        },
    }

    result = await service._register_single_udf("complete", alias)

    assert result["registered"] is True
    create = next(sql for sql in engine.statements if sql.startswith("CREATE GLOBAL FUNCTION"))
    config = json.loads(create.split("ai_query(prompt, '", 1)[1].rsplit("')", 1)[0])
    assert config == {
        "max_tokens": 64,
        "timeout_ms": 20000,
        "model": "provider-model",
        "api_key": alias_token("alias-1"),
        "endpoint": "http://nova-query:8000/api/v1/internal/llm/chat/completions",
    }
    assert PROVIDER_KEY not in "".join(engine.statements)


async def test_provider_without_an_endpoint_is_not_registered(monkeypatch):
    from app.modules.llm_functions.service import LLMFunctionService

    service = LLMFunctionService()
    monkeypatch.setattr(
        service, "_get_provider", AsyncMock(return_value={**PROVIDER, "endpoint": ""})
    )
    connect = AsyncMock()
    monkeypatch.setattr(service, "_connect", connect)

    result = await service._register_single_udf("complete", dict(ALIAS))

    assert result["registered"] is False and result["error"] == "Provider has no endpoint"
    connect.assert_not_awaited()


# ── Leaving matching functions alone ────────────────────────────────────────


def _engine_rows(bodies: dict[str, str]) -> list[tuple]:
    return [
        (f"{name.lower()}(VARCHAR)", "VARCHAR", "SQL", "NULL", body)
        for name, body in bodies.items()
    ]


@pytest.fixture
def registered(monkeypatch):
    """An engine whose seven functions match one alias plus six placeholders."""
    from app.modules.llm_functions.service import UDF_TEMPLATES, LLMFunctionService

    monkeypatch.setattr(settings, "LLM_GATEWAY_URL", "http://nova-query:8000")
    service = LLMFunctionService()
    bodies = {
        template["function_name"]: "concat('ERROR: X not configured. ', txt)"
        for template in UDF_TEMPLATES.values()
    }
    bodies["AI_COMPLETE"] = (
        'ai_query(`prompt`, \'{"model": "provider-model","api_key": "'
        + alias_token("alias-1")
        + '","endpoint": "http://nova-query:8000'
        + gateway.GATEWAY_PATH
        + "\"}')"
    )

    class Cursor(_Engine):
        async def fetchall(self):
            return _engine_rows(bodies)

    aliases = [dict(ALIAS)]
    monkeypatch.setattr(service, "_connect", AsyncMock(return_value=Cursor()))
    monkeypatch.setattr(service, "list_aliases", AsyncMock(return_value=aliases))
    return service, bodies, aliases


async def test_matching_functions_are_current(registered):
    service, _, _ = registered

    assert await service.registration_is_current() is True


@pytest.mark.parametrize(
    "drift",
    [
        "missing",
        "old_provider_key",
        "other_gateway",
        "other_model",
        "placeholder_left",
        "new_alias",
    ],
)
async def test_any_drift_makes_registration_necessary(monkeypatch, registered, drift):
    service, bodies, aliases = registered
    if drift == "missing":
        del bodies["AI_SENTIMENT"]
    elif drift == "old_provider_key":
        bodies["AI_COMPLETE"] = bodies["AI_COMPLETE"].replace(alias_token("alias-1"), PROVIDER_KEY)
    elif drift == "other_gateway":
        monkeypatch.setattr(settings, "LLM_GATEWAY_URL", "http://nova-backend:8000")
    elif drift == "other_model":
        aliases[0]["model_name"] = "newer-model"
    elif drift == "placeholder_left":
        bodies["AI_COMPLETE"] = "concat('ERROR: AI_COMPLETE not configured. ', prompt)"
    elif drift == "new_alias":
        aliases.append({**ALIAS, "id": "alias-2", "function_type": "summarize"})

    assert await service.registration_is_current() is False


async def test_inactive_alias_does_not_force_registration(registered):
    service, bodies, aliases = registered
    aliases[0]["is_active"] = False
    bodies["AI_COMPLETE"] = "anything left from before"

    assert await service.registration_is_current() is True
