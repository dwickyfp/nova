"""Auto keeps a scoped run alive across transient specialist metadata reads."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.modules.agents import harness_worker as worker_module
from app.modules.agents.auto_planner import AgentDiscoveryUnavailable
from app.modules.agents.harness_worker import MAX_DISCOVERY_ATTEMPTS, AgentHarnessWorker


def _root(attempts: int = 0) -> dict[str, Any]:
    return {
        "run_id": "root", "root_run_id": None, "agent_id": "__auto__",
        "owner_name": "alice", "role_name": "analyst", "thread_id": "thread",
        "session_id": "session", "security_version": 1, "depth": 0,
        "status": "running", "lease_owner": "worker-1", "generation": 1,
        "checkpoint": {"phase": "plan", "discovery_attempts": attempts},
    }


@pytest.mark.asyncio
async def test_transient_discovery_requeues_then_completes_without_false_no_specialist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _root()
    events: list[tuple[str, dict[str, Any]]] = []
    transitions: list[dict[str, Any]] = []

    class Repository:
        async def get(self, run_id: str) -> dict[str, Any]:
            assert run_id == "root"
            return run

        async def heartbeat(self, run_id: str, lease_owner: str) -> None:
            assert run_id == "root"

        async def transition(self, run_id: str, **kwargs: Any) -> bool:
            assert run_id == "root"
            assert kwargs["lease_owner"] == run["lease_owner"]
            assert kwargs["generation"] == run["generation"]
            transitions.append(kwargs)
            run["status"] = kwargs["to_status"]
            if "checkpoint" in kwargs:
                run["checkpoint"] = kwargs["checkpoint"]
            return True

        async def event(
            self, root_id: str, run_id: str, kind: str, payload: dict[str, Any]
        ) -> None:
            assert root_id == run_id == "root"
            events.append((kind, payload))

    worker = AgentHarnessWorker(Repository())
    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value={"username": "alice"}))
    monkeypatch.setattr(worker_module.asyncio, "sleep", AsyncMock())
    calls = 0

    async def coordinate(*_args: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AgentDiscoveryUnavailable("Metadata read was inconsistent")
        run["status"] = "completed"
        events.append(("agent_completed", {"answer": "Both specialists reported."}))

    monkeypatch.setattr(worker, "_coordinate", coordinate)

    await worker._process_claimed("root", "worker-1")
    assert run["status"] == "queued"
    assert run["checkpoint"] == {"phase": "plan", "discovery_attempts": 1}
    assert transitions[0]["to_status"] == "queued"
    assert transitions[0]["error_class"] == "capability_metadata_unavailable"

    run.update(status="running", lease_owner="worker-2", generation=2)
    await worker._process_claimed("root", "worker-2")

    assert run["status"] == "completed"
    assert [kind for kind, _ in events] == [
        "agent_started", "agent_waiting", "agent_started", "agent_completed"
    ]
    assert events[1][1] == {"reason": "capability_retry", "attempt": 1}
    assert "No accessible specialist" not in str(events)


@pytest.mark.asyncio
async def test_discovery_retry_budget_fails_with_explicit_metadata_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _root(MAX_DISCOVERY_ATTEMPTS - 1)
    transitions: list[dict[str, Any]] = []
    events: list[tuple[str, dict[str, Any]]] = []

    class Repository:
        async def get(self, run_id: str) -> dict[str, Any]:
            return run

        async def heartbeat(self, run_id: str, lease_owner: str) -> None:
            return None

        async def transition(self, run_id: str, **kwargs: Any) -> bool:
            transitions.append(kwargs)
            run["status"] = kwargs["to_status"]
            return True

        async def event(
            self, root_id: str, run_id: str, kind: str, payload: dict[str, Any]
        ) -> None:
            events.append((kind, payload))

    worker = AgentHarnessWorker(Repository())
    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value={"username": "alice"}))
    monkeypatch.setattr(worker, "_coordinate", AsyncMock(
        side_effect=AgentDiscoveryUnavailable("Metadata read was inconsistent")
    ))
    monkeypatch.setattr(worker_module.asyncio, "sleep", AsyncMock())

    await worker._process_claimed("root", "worker-1")

    assert run["status"] == "failed"
    assert transitions[0]["to_status"] == "failed"
    assert transitions[0]["error_class"] == "capability_metadata_unavailable"
    assert events[-1] == (
        "agent_failed", {"error_class": "capability_metadata_unavailable"}
    )
