import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from app.modules.ai_ml import decision_settings as config
from app.modules.assistant import decision as module
from app.modules.assistant.decision import DecisionSession, parse_choice, question


def settings(**kwargs):
    return config.DecisionSettings(
        enabled=True,
        decision_model_id="decision",
        light_model_id="light",
        heavy_model_id="heavy",
        **kwargs,
    )


def answer(label="a", probability=0.95, confidence=0.9):
    return {
        "type": "choice",
        "choice": label,
        "confidence": confidence,
        "probabilities": {"a": probability, "none": 1 - probability},
    }


OPTIONS = {"a": "A", "none": "No fit"}
QUESTIONS = {"selection": question("Choose a", OPTIONS)}


@pytest.mark.parametrize(
    "patch",
    [
        {"choice": "invented"},
        {"type": "score"},
        {"confidence": True},
        {"confidence": float("nan")},
        {"confidence": -0.1},
        {"probabilities": {"a": 1}},
        {"probabilities": {"a": 0.9, "none": 0.9}},
        {"probabilities": {"a": float("inf"), "none": 0}},
        {"probabilities": {"a": 0.1, "none": 0.9}},
    ],
)
def test_invalid_choices_rejected(patch):
    with pytest.raises(ValueError):
        parse_choice({**answer(), **patch}, OPTIONS)


async def test_disabled_does_not_read_credentials_or_send():
    session = DecisionSession(config.DecisionSettings())
    session._post = AsyncMock(side_effect=AssertionError("Unexpected inference"))
    assert await session.ask("test", {}, QUESTIONS) == {}
    session._post.assert_not_called()
    assert session.trace == []


@pytest.mark.parametrize(
    "reply",
    [
        {"answers": {"selection": answer(probability=0.6)}},
        {"answers": {"selection": answer(confidence=0.2)}},
        {"answers": {}},
        {"answers": {"selection": answer(), "extra": answer()}},
    ],
)
async def test_uncertain_absent_and_no_fit_choices_fall_back(reply):
    session = DecisionSession(settings())
    session._post = AsyncMock(return_value=reply)
    assert await session.choose("test", {}, "Choose", {"a": "A"}) is None


async def test_timeout_budget_and_no_retry():
    session = DecisionSession(settings(timeout_seconds=0.5))
    session._post = AsyncMock(side_effect=TimeoutError("Bearer secret in URL"))
    for _ in range(9):
        assert await session.ask("test", {}, QUESTIONS) == {}
    assert session._post.await_count == module.MAX_CALLS
    assert "secret" not in json.dumps(session.trace)
    assert session.trace[-1]["reason"] == "budget"


async def test_cancellation_propagates():
    session = DecisionSession(settings())
    session._post = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await session.ask("test", {}, QUESTIONS)


async def test_oversized_state_falls_back_without_network():
    session = DecisionSession(settings())
    session._post = AsyncMock()
    assert await session.ask("test", "x" * 60_001, QUESTIONS) == {}
    session._post.assert_not_called()


@pytest.mark.parametrize("redirect", [False, True])
async def test_exact_endpoint_and_no_redirect_credentials(monkeypatch, redirect):
    endpoint = "https://decision.example/custom/infer/?v=2"
    monkeypatch.setattr(
        module,
        "registered_model",
        AsyncMock(
            return_value=(
                {"name": "jev", "id": "decision"},
                {"id": "provider", "endpoint": endpoint},
            )
        ),
    )
    monkeypatch.setattr(module.ai_service, "get_provider_api_key", AsyncMock(return_value="secret"))
    requests = []

    def respond(request):
        requests.append(request)
        assert str(request.url) == endpoint
        assert json.loads(request.content)["model"] == "jev"
        if redirect:
            return httpx.Response(307, headers={"Location": "https://other.example/steal"})
        return httpx.Response(200, json={"answers": {"selection": answer()}})

    monkeypatch.setattr(
        module,
        "guarded_async_client",
        lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(respond),
            follow_redirects=True,
        ),
    )
    session = DecisionSession(settings())
    result = await session.ask("test", {}, QUESTIONS)
    assert len(requests) == 1
    assert bool(result) is not redirect
    assert "secret" not in json.dumps(session.trace)


async def test_provider_revalidated_on_every_inference(monkeypatch):
    model = AsyncMock(return_value={"id": "d", "provider_id": "p", "type": "decision"})
    provider = AsyncMock(return_value={"id": "p", "type": "decision", "is_active": False})
    monkeypatch.setattr(config.ai_service, "get_model", model)
    monkeypatch.setattr(config.ai_service, "get_provider", provider)
    with pytest.raises(ValueError, match="unavailable"):
        await config.registered_model("d", "decision")


async def test_settings_round_trip_and_disable_after_model_deleted(monkeypatch):
    execute = AsyncMock(return_value={"rows": []})
    monkeypatch.setattr(config.db, "execute_system", execute)
    assert not (await config.read_decision_settings()).enabled
    invalid = AsyncMock(side_effect=ValueError("gone"))
    monkeypatch.setattr(config, "registered_model", invalid)
    disabled = settings().model_copy(update={"enabled": False})
    await config.save_decision_settings(disabled)
    invalid.assert_not_called()
    stored = execute.call_args.args[1][2]
    execute.return_value = {"rows": [[stored]]}
    assert await config.read_decision_settings() == disabled
    with pytest.raises(ValueError, match="gone"):
        await config.save_decision_settings(settings())


def test_enabling_requires_all_models():
    with pytest.raises(ValidationError):
        config.DecisionSettings(enabled=True)


async def test_model_routing_failure_keeps_original_model(monkeypatch):
    session = DecisionSession(settings())
    session.choose = AsyncMock(return_value="heavy")
    monkeypatch.setattr(module, "registered_model", AsyncMock(side_effect=ValueError("inactive")))
    assert await session.workload("Audit revenue") is None




async def test_response_body_limit(monkeypatch):
    monkeypatch.setattr(
        module,
        "registered_model",
        AsyncMock(
            return_value=(
                {"name": "jev"},
                {"id": "p", "endpoint": "https://decision.example/full"},
            )
        ),
    )
    monkeypatch.setattr(module.ai_service, "get_provider_api_key", AsyncMock(return_value=None))
    monkeypatch.setattr(
        module,
        "guarded_async_client",
        lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"x" * 200_001)
            ),
        ),
    )
    assert await DecisionSession(settings()).ask("test", {}, QUESTIONS) == {}


async def test_confident_no_fit_is_distinct_from_transport_failure():
    session = DecisionSession(settings())
    session._post = AsyncMock(
        return_value={
            "answers": {
                "selection": answer(label="none", probability=0.02),
            }
        }
    )
    assert await session.choose("test", {}, "Choose", {"a": "A"}) == "none"
    session._post.side_effect = TimeoutError()
    assert await session.choose("test", {}, "Choose", {"a": "A"}) is None


async def test_exhausted_loop_budget_prevents_inference():
    session = DecisionSession(settings())
    session.time_remaining = lambda: 0
    session._post = AsyncMock()
    assert await session.ask("test", {}, QUESTIONS) == {}
    session._post.assert_not_called()


async def test_agent_ranking_preserves_semantic_owners_and_candidate_set():
    from types import SimpleNamespace

    candidates = [
        SimpleNamespace(agent_id=key, prompt_view=lambda key=key: {"id": key})
        for key in ("owner", "similar", "unrelated")
    ]
    session = DecisionSession(settings())
    session.relevance = AsyncMock(return_value={"owner": 0, "similar": 2, "invented": 2})
    result = await module.rank_agents(
        session, "revenue", candidates, semantic_matches=[{"agent_id": "owner"}]
    )
    assert result[0].agent_id == "owner"
    assert {item.agent_id for item in result} == {"owner", "similar", "unrelated"}


