"""A delayed run snapshot cannot make an older Auto worker settle a newer lease."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.harness_worker import AgentHarnessWorker


class LeaseRepository:
    def __init__(self) -> None:
        self.run = {
            "run_id": "auto-lease-eval",
            "root_run_id": None,
            "depth": 0,
            "status": "queued",
            "lease_owner": None,
            "generation": 0,
            "checkpoint": {"phase": "fresh"},
        }
        self.stale_reads = 0
        self.events: list[str] = []
        self.transitions: list[dict] = []
        self.refreshes: list[tuple[str, int]] = []

    async def claim(self, _run_id: str, lease_owner: str) -> bool:
        self.run.update(status="running", lease_owner=lease_owner, generation=1)
        return True

    async def get(self, _run_id: str) -> dict:
        if self.stale_reads:
            self.stale_reads -= 1
            return {
                **self.run,
                "lease_owner": "previous-worker",
                "generation": 0,
                "checkpoint": {"phase": "stale"},
            }
        return dict(self.run)

    async def event(self, _root_id: str, _run_id: str, kind: str, _payload: dict) -> str:
        self.events.append(kind)
        return str(len(self.events))

    async def heartbeat(self, _run_id: str, _lease_owner: str) -> None:
        return None

    async def refresh_owned_lease(
        self, _run_id: str, *, lease_owner: str, generation: int
    ) -> bool:
        self.refreshes.append((lease_owner, generation))
        return (
            self.run["status"] == "running"
            and self.run["lease_owner"] == lease_owner
            and self.run["generation"] == generation
        )

    async def transition(self, _run_id: str, **arguments) -> bool:
        self.transitions.append(arguments)
        if (
            self.run["status"] != arguments["from_status"]
            or self.run["lease_owner"] != arguments["lease_owner"]
            or self.run["generation"] != arguments["generation"]
        ):
            return False
        self.run["status"] = arguments["to_status"]
        return True

    def take_over(self) -> None:
        self.run.update(lease_owner="replacement-worker", generation=2)


@pytest.mark.asyncio
async def test_stale_lease_reads_recover_before_auto_root_settles(monkeypatch) -> None:
    repository = LeaseRepository()
    repository.stale_reads = 1
    worker = AgentHarnessWorker(repository)
    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value={"username": "eval"}))
    observed: list[dict] = []

    async def coordinate(run: dict, _user: dict, _cancelled: asyncio.Event) -> None:
        observed.append(run["checkpoint"])
        repository.stale_reads = 100
        await worker._assert_running(run)
        assert await repository.transition(
            run["run_id"],
            from_status="running",
            to_status="completed",
            lease_owner=run["lease_owner"],
            generation=run["generation"],
        )

    monkeypatch.setattr(worker, "_coordinate", coordinate)
    await worker.process(repository.run["run_id"], "eval-worker")

    assert observed == [{"phase": "fresh"}]
    assert repository.run["status"] == "completed"
    assert repository.events == ["agent_started"]
    assert all(item["to_status"] != "failed" for item in repository.transitions)
    assert repository.refreshes == [
        (repository.transitions[0]["lease_owner"], repository.transitions[0]["generation"])
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["lease_guard", "unexpected_exception"])
async def test_replaced_worker_cannot_fail_or_overwrite_new_lease(
    monkeypatch, failure: str
) -> None:
    repository = LeaseRepository()
    worker = AgentHarnessWorker(repository)
    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value={"username": "eval"}))

    async def coordinate(run: dict, _user: dict, _cancelled: asyncio.Event) -> None:
        repository.take_over()
        if failure == "lease_guard":
            await worker._assert_running(run)
        else:
            raise ValueError("older worker failed after takeover")

    monkeypatch.setattr(worker, "_coordinate", coordinate)
    await worker.process(repository.run["run_id"], "eval-worker")

    assert repository.run["status"] == "running"
    assert repository.run["lease_owner"] == "replacement-worker"
    assert repository.run["generation"] == 2
    assert repository.events == ["agent_started"]
    if failure == "unexpected_exception":
        assert len(repository.transitions) == 1
        assert repository.transitions[0]["to_status"] == "failed"
        assert repository.transitions[0]["generation"] == 1
    else:
        assert not repository.transitions
        assert len(repository.refreshes) == 1
