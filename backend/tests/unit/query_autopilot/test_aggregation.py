from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from app.modules.query_autopilot.aggregation import aggregate
from app.modules.query_autopilot.models import Observation, Scope, digest
from tests.unit.query_autopilot.test_service import MemoryRepository


class TimedRepository(MemoryRepository):
    def __init__(self, now):
        super().__init__()
        self.now = now
        self.times = {}
        self.columns = {}

    async def put(self, kind, identifier, payload, **metadata):
        self.times[kind, identifier] = (
            metadata.get("created_at", self.now), self.now,
        )
        self.columns[kind, identifier] = metadata
        return await super().put(kind, identifier, payload, **metadata)

    async def page(self, kind, *, after="", limit=100, **filters):
        rows = []
        for (table, identifier), value in sorted(self.records.items()):
            if table != kind or identifier <= after:
                continue
            created, updated = self.times[table, identifier]
            if filters.get("since") and updated < filters["since"]:
                continue
            if filters.get("created_since") and created < filters["created_since"]:
                continue
            if filters.get("before") and created >= filters["before"]:
                continue
            if any(
                self.columns[table, identifier].get(key, value.get(key)) != selected
                for key, selected in filters.items()
                if key in {"family_id", "cohort_id", "state"} and selected is not None
            ):
                continue
            rows.append(deepcopy(value))
        return rows[:limit]


async def observe(repo, scope, count, observed):
    for index in range(count):
        item = Observation(
            id=f"{scope.principal}-{index:04d}", family_id="shared-shape", scope=scope,
            observed_at=observed, source="http", status="success", total_ms=10,
        )
        await repo.put(
            "observations", item.id, item.model_dump(mode="json"),
            family_id=item.family_id, cohort_id=scope.cohort_id, created_at=observed,
        )


async def test_idle_closed_window_ages_without_new_observations(monkeypatch):
    now = datetime(2026, 10, 1, 10, 35, tzinfo=UTC)
    repo = TimedRepository(now)
    monkeypatch.setattr(
        "app.modules.query_autopilot.enrichment.measured_facts",
        AsyncMock(return_value=({}, ())),
    )
    alice = Scope(principal="alice", active_role="analyst", security_context_version=1)
    bob = alice.model_copy(update={"principal": "bob"})
    observed = now - timedelta(minutes=25)
    await observe(repo, alice, 100, observed)
    await observe(repo, bob, 1, observed)
    await aggregate(repo, now=now)
    alice_key = digest(["shared-shape", alice.cohort_id])
    bob_key = digest(["shared-shape", bob.cohort_id])
    original = await repo.get("families", alice_key)
    assert original["baseline"]["current_count"] == 100
    assert {f["detector"] for f in original["findings"]} == {"high_frequency"}
    assert (await repo.get("families", bob_key))["baseline"]["current_count"] == 1

    repo.now += timedelta(minutes=3)
    await aggregate(repo, now=repo.now)
    repo.now += timedelta(minutes=40)
    result = await aggregate(repo, now=repo.now)
    aged = await repo.get("families", alice_key)
    assert result["windows"] == 0
    assert aged["baseline"]["current_count"] == 0
    assert aged["findings"] == []
    assert aged["last_observed_at"] == original["last_observed_at"]
    assert aged["baseline"]["historical_count"] == 100
    assert (await repo.get("families", bob_key))["baseline"]["historical_count"] == 1

    repo.now += timedelta(minutes=15)
    assert (await aggregate(repo, now=repo.now))["families"] == 0


async def test_late_arrival_recomputes_history_without_double_counting(monkeypatch):
    now = datetime(2026, 10, 1, 10, 35, tzinfo=UTC)
    repo = TimedRepository(now)
    monkeypatch.setattr(
        "app.modules.query_autopilot.enrichment.measured_facts",
        AsyncMock(return_value=({}, ())),
    )
    scope = Scope(principal="alice", active_role="analyst", security_context_version=1)
    observed = now - timedelta(minutes=25)
    await observe(repo, scope, 100, observed)
    await aggregate(repo, now=now)
    repo.now += timedelta(minutes=40)
    await observe(repo, scope, 101, observed)
    await aggregate(repo, now=repo.now)
    key = digest(["shared-shape", scope.cohort_id])
    assert (await repo.get("families", key))["baseline"]["historical_count"] == 101
    await aggregate(repo, now=repo.now)
    assert (await repo.get("families", key))["baseline"]["historical_count"] == 101
    assert (await repo.get("families", key))["baseline"]["current_count"] == 0
