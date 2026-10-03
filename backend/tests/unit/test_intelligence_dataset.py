"""Benchmark integrity and business relationships independent of agent answers."""

from pathlib import Path

from tests.benchmark.business_intelligence.generate import PROFILES, generate, tables


def test_small_fixture_is_reproducible_and_has_seven_domains(tmp_path):
    first, second = generate(tmp_path / "first"), generate(tmp_path / "second")
    assert first == second
    assert first["tables"]["customers"]["rows"] == 1000
    assert first["tables"]["orders"]["rows"] == 5000
    assert {
        "orders",
        "inventory_snapshots",
        "campaign_exposures",
        "checkout_events",
        "payment_attempts",
        "support_tickets",
        "service_metrics",
    } <= first["tables"].keys()
    assert not any("truth" in name or "outcome" in name for name in first["tables"])


def test_profile_scale_and_checkout_experiment_references():
    assert PROFILES["standard"][:2] == (25000, 150000)
    assert PROFILES["full"][:2] == (50000, 300000)
    data = dict(tables("small"))
    sessions = {row["session_id"] for row in data["web_sessions"]}
    assignments = list(data["campaign_exposures"])
    assert len({row["customer_id"] for row in assignments}) == 1000
    assert {row["session_id"] for row in assignments} <= sessions
    rates = [
        sum(row["converted"] for row in assignments if row["treatment"] == arm)
        / sum(row["treatment"] == arm for row in assignments)
        for arm in (0, 1)
    ]
    assert 0.12 < rates[1] - rates[0] < 0.28
    for row in data["checkout_events"]:
        assert row["session_id"] in sessions
        assert row["converted"] <= row["shipping_selected"]


def test_gold_queries_do_not_import_runtime_compiler():
    from tests.benchmark.business_intelligence.gold import queries

    source = Path(queries.__file__).read_text()
    assert "from app." not in source and "import app." not in source
    sql, params = queries.recognized_revenue("2026-03-01", "2026-03-08", city="Jakarta")
    assert "posted_at < %s" in sql and "GROUP BY order_id" in sql
    assert params == ["2026-03-26", "2026-03-01", "2026-03-08", "Jakarta"]


def test_observations_do_not_contain_future_fulfillment_or_unsupported_health_events():
    data = dict(tables("small"))
    orders = {row["order_id"]: row for row in data["orders"]}
    for row in data["fulfillment_events"]:
        assert row["occurred_at"] < "2026-03-26"
        assert (row["status"] == "shipped") == (
            orders[row["order_id"]]["status"] in {"completed", "fulfilled"}
        )
    affected = {
        row["customer_id"] for row in orders.values() if row["reason"] == "payment_hard_decline"
    }
    assert {row["customer_id"] for row in data["customer_health_events"]} == affected


async def test_live_fixture_verification_rejects_changed_rows_not_just_counts(
    tmp_path, monkeypatch
):
    import csv
    import hashlib
    import json
    from datetime import datetime
    from decimal import Decimal

    import pytest

    from tests.benchmark.business_intelligence import dataset

    sample = {"id": 1, "amount": 12.5, "observed_at": "2026-01-01T00:00:00", "city": "Jakarta"}
    path = tmp_path / "observations.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(sample))
        writer.writeheader()
        writer.writerow(sample)
    manifest = {
        "schema": dataset.DATABASE,
        "profile": "small",
        "tables": {
            "observations": {
                "columns": list(sample),
                "rows": 1,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        },
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(dataset, "tables", lambda _profile: [("observations", iter([sample]))])
    observed = [[1, Decimal("12.500000"), datetime(2026, 1, 1), "Jakarta"]]

    async def execute(sql, params):
        assert "NOVA_INTELLIGENCE_BENCH.observations" in sql
        assert params == [0]
        return {"rows": observed}

    assert await dataset.verify_observations(execute, tmp_path) == manifest
    observed[0][-1] = "Bandung"
    with pytest.raises(ValueError, match="Live observations differ"):
        await dataset.verify_observations(execute, tmp_path)
    observed.clear()
    with pytest.raises(ValueError, match="Live observations differ"):
        await dataset.verify_observations(execute, tmp_path)
    path.write_text(path.read_text().replace("Jakarta", "Bandung"))
    with pytest.raises(ValueError, match="Frozen CSV changed"):
        await dataset.verify_observations(execute, tmp_path)


async def test_future_outcomes_only_write_to_separate_training_warehouse():
    from unittest.mock import AsyncMock

    from tests.benchmark.business_intelligence.outcome_training import (
        TRAINING_DATABASE,
        reveal_future_orders,
    )

    row = next(dict(tables("small"))["orders"])
    execute = AsyncMock(return_value={"rows": [list(row.values())]})
    manifest = await reveal_future_orders(execute, days=2)
    assert manifest == {
        "start": "2026-03-26",
        "end": "2026-03-28",
        "rows": 2,
        "attribution": "observed_after",
    }
    calls = execute.await_args_list
    assert len(calls) == 2
    assert calls[0].args[0].startswith("SELECT")
    assert calls[1].args[0].startswith(f"INSERT INTO {TRAINING_DATABASE}.orders")
    assert 1_000_000_000_000 in calls[1].args[1] and 1_000_001_000_000 in calls[1].args[1]
    values = calls[1].args[1]
    future = dict(zip(row, values[: len(row)], strict=True))
    assert future["status"] == "completed" and future["reason"] == ""
    assert future["refund_amount"] == 0 and future["complete"] == 1


async def test_future_fixture_refuses_to_replace_an_existing_training_store():
    from unittest.mock import AsyncMock

    import pytest

    from tests.benchmark.business_intelligence.outcome_training import prepare

    execute = AsyncMock(side_effect=[{"rows": []}, {"rows": [["orders"]]}])
    with pytest.raises(ValueError, match="already exists"):
        await prepare(execute)
    assert execute.await_count == 2


def test_training_catalog_sources_are_isolated_and_database_is_allowlisted():
    import pytest

    from tests.benchmark.business_intelligence.model import starter_definition
    from tests.benchmark.business_intelligence.outcome_training import TRAINING_DATABASE

    definition = starter_definition(database=TRAINING_DATABASE)
    assert all(
        item["source"].startswith(TRAINING_DATABASE + ".") for item in definition["datasets"]
    )
    with pytest.raises(ValueError, match="isolated benchmark"):
        starter_definition(database="NOVA_SYSTEM")
