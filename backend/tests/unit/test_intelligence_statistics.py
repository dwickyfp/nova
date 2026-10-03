import pytest

from tests.benchmark.business_intelligence.statistics import paired_improvement, summarize, wilson


def result(case, repetition, value):
    return {
        "case_id": case,
        "repetition": repetition,
        "learning_sensitive": True,
        "scores": {"answer_accuracy": value},
    }


def test_unavailable_is_not_zero_or_a_pass():
    report = summarize([result("case", 0, None)])
    assert report["dimensions"]["answer_accuracy"] == {
        "value": None,
        "scored": 0,
        "unavailable": 1,
        "confidence_interval_95": None,
    }
    assert report["consistency"] is None and report["brier"] is None
    assert wilson(0, 0) is None
    assert wilson(100, 100)[0] > 0.96


def test_pairing_preserves_case_cluster_and_reports_flakiness():
    before = [result(str(i), r, False) for i in range(20) for r in range(3)]
    after = [result(str(i), r, i < 10) for i in range(20) for r in range(3)]
    improvement = paired_improvement(before, after)
    assert improvement["absolute_change"] == 0.5
    assert improvement["confidence_interval_95"][0] > 0 and improvement["target_met"]
    after[0]["scores"]["answer_accuracy"] = False
    assert summarize(after)["flaky_cases"] == ["0"]
    with pytest.raises(ValueError, match="exactly"):
        paired_improvement(before, after[:-1])
    with pytest.raises(ValueError, match="Duplicate"):
        paired_improvement(before, [*after, after[0]])


def test_live_calls_are_reserved_before_provider_execution():
    from tests.benchmark.business_intelligence.live_budget import LiveBudget

    budget = LiveBudget(
        maximum_dollars=0.1,
        input_dollars_per_million=10,
        output_dollars_per_million=10,
        maximum_calls=3,
    )
    with pytest.raises(RuntimeError, match="cost"):
        budget.reserve([{"content": "small"}], None, 3)
    assert budget.calls == 0 and budget.reserved_dollars == 0
    budget.maximum_dollars = 1
    budget.reserve([{"content": "small"}], None, 3)
    assert budget.calls == 3 and 0 < budget.reserved_dollars < 1
    with pytest.raises(RuntimeError, match="call"):
        budget.reserve([], None, 1)


def test_live_runner_requires_explicit_spend_authorization():
    from tests.benchmark.business_intelligence.run import arguments

    with pytest.raises(SystemExit):
        arguments(["live", "--dataset-manifest", "data/manifest.json", "--frozen", "frozen"])
    with pytest.raises(SystemExit):
        arguments(
            [
                "live",
                "--dataset-manifest",
                "data/manifest.json",
                "--frozen",
                "frozen",
                "--allow-paid-calls",
                "--repetitions",
                "2",
                "--output",
                "reports",
            ]
        )


async def test_live_guard_rejects_unfrozen_provider_before_paid_transport(monkeypatch):
    from unittest.mock import AsyncMock

    from app.modules.assistant.provider import AssistantProviderClient, ProviderConfig
    from tests.benchmark.business_intelligence.live_budget import LiveBudget

    transport = AsyncMock()
    monkeypatch.setattr(AssistantProviderClient, "complete", transport)
    budget = LiveBudget(1, 1, 1, provider_id="expected", model="fixed")
    config = ProviderConfig(
        provider_id="other", model="fixed", endpoint="https://fixture.invalid", api_key="fixture"
    )
    with budget.guard(), pytest.raises(RuntimeError, match="Provider changed"):
        await AssistantProviderClient().complete(messages=[], provider=config)
    transport.assert_not_awaited()
    assert budget.calls == 0


async def test_live_guard_rejects_changed_endpoint_before_paid_transport(monkeypatch):
    from dataclasses import replace
    from unittest.mock import AsyncMock

    from app.modules.assistant.provider import AssistantProviderClient, ProviderConfig
    from tests.benchmark.business_intelligence.live_budget import LiveBudget, transport_fingerprint

    transport = AsyncMock()
    monkeypatch.setattr(AssistantProviderClient, "complete", transport)
    config = ProviderConfig(
        provider_id="expected",
        model="fixed",
        endpoint="https://original.invalid/v1",
        api_key="fixture",
    )
    budget = LiveBudget(
        1,
        1,
        1,
        provider_id="expected",
        model="fixed",
        transport_fingerprint=transport_fingerprint(config),
    )
    with budget.guard(), pytest.raises(RuntimeError, match="Provider changed"):
        await AssistantProviderClient().complete(
            messages=[], provider=replace(config, endpoint="https://changed.invalid/v1")
        )
    transport.assert_not_awaited()
    assert budget.calls == 0
    credential_url = replace(
        config, endpoint="https://user:password@original.invalid/v1"
    )
    assert transport_fingerprint(credential_url) == transport_fingerprint(config)


async def test_live_guard_reserves_cost_caps_output_and_disables_unbudgeted_embeddings(monkeypatch):
    from unittest.mock import AsyncMock

    from app.modules.ai_ml.embeddings import EmbeddingError, EmbeddingService
    from app.modules.assistant.provider import AssistantProviderClient, ProviderConfig
    from tests.benchmark.business_intelligence.live_budget import LiveBudget

    transport = AsyncMock(return_value={"content": "completed"})
    monkeypatch.setattr(AssistantProviderClient, "complete", transport)
    budget = LiveBudget(1, 1, 1, maximum_calls=6)
    config = ProviderConfig(
        provider_id="expected", model="fixed", endpoint="https://fixture.invalid", api_key="fixture"
    )
    with budget.guard():
        client = AssistantProviderClient()
        await client.complete(messages=[{"role": "user", "content": "question"}], provider=config)
        assert budget.calls == client._max_attempts
        assert client._request_body(config, [], None)["max_tokens"] == 2048
        with pytest.raises(EmbeddingError, match="lexical"):
            await EmbeddingService().embed_batch(["memory"], None)
    transport.assert_awaited_once()
