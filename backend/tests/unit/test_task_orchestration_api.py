"""Unit tests for the read-only orchestration API (NOVA-54 / PR 4a).

The endpoints read `CONFIG_TASK*` through the repository, which is replaced here
with an in-memory fake, and the authenticated user is supplied through a
dependency override. No engine, no Redis.

These pin the contract the UI depends on, the ownership scoping the system-pool
read would otherwise bypass, and the credential-invisibility rules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.task_orchestration import router as orch_router

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class FakeRepository:
    """The repository surface the read API uses, in memory."""

    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self.graph_runs: dict[str, dict[str, Any]] = {}
        self.task_runs: list[dict[str, Any]] = []

    def add_task(
        self,
        name: str,
        *,
        owner: str = "alice",
        when_expr: str | None = None,
        schedule_kind: str = "manual",
        schedule_expr: str | None = None,
        overlap_policy: str = "skip",
    ) -> str:
        task_id = f"id_{name}"
        self.tasks[name] = {
            "id": task_id,
            "name": name,
            "definition": "INSERT INTO t SELECT 1",
            "database_name": "db1",
            "created_by": owner,
            "schedule_kind": schedule_kind,
            "schedule_expr": schedule_expr,
            "timezone": "UTC",
            "when_expr": when_expr,
            "overlap_policy": overlap_policy,
            "owner_role": None,
        }
        return task_id

    def add_edge(self, graph_id: str, parent: str, child: str, kind: str = "after") -> None:
        self.edges.append(
            {
                "id": f"e_{graph_id}_{parent}_{child}",
                "graph_id": graph_id,
                "parent_task": parent,
                "child_task": child,
                "edge_kind": kind,
            }
        )

    def add_graph_run(
        self,
        run_id: str,
        graph_id: str,
        *,
        state: str = "success",
        overlap_policy: str = "skip",
        started_at: datetime | None = NOW,
    ) -> None:
        self.graph_runs[run_id] = {
            "id": run_id,
            "graph_id": graph_id,
            "trigger_type": "schedule",
            "state": state,
            "overlap_policy": overlap_policy,
            "wal_marks": None,
            "started_at": started_at,
            "heartbeat_at": None,
            "finished_at": None,
        }

    def add_task_run(
        self,
        run_id: str,
        graph_run_id: str,
        task_id: str,
        *,
        state: str = "success",
        attempt: int = 1,
        error_message: str | None = None,
    ) -> None:
        self.task_runs.append(
            {
                "id": run_id,
                "graph_run_id": graph_run_id,
                "task_id": task_id,
                "attempt": attempt,
                "state": state,
                "delegated": True,
                "starrocks_query_id": None,
                "error_message": error_message,
                "started_at": NOW,
                "heartbeat_at": None,
                "finished_at": None,
            }
        )

    # ── repository surface ─────────────────────────────────────
    async def list_tasks(self, graph_id: str | None = None):
        return list(self.tasks.values())

    async def list_all_edges(self):
        return list(self.edges)

    async def list_edges(self, graph_id: str):
        return [e for e in self.edges if e["graph_id"] == graph_id]

    async def list_graph_ids(self) -> list[str]:
        graph_ids = sorted({str(e["graph_id"]) for e in self.edges})
        referenced = {
            str(endpoint)
            for e in self.edges
            for endpoint in (e["parent_task"], e["child_task"])
        }
        standalone = sorted(
            str(t["id"]) for t in self.tasks.values() if str(t["name"]) not in referenced
        )
        return [*graph_ids, *[gid for gid in standalone if gid not in graph_ids]]

    async def get_tasks_by_names(self, names: list[str]):
        wanted = set(names)
        return [t for t in self.tasks.values() if str(t["name"]) in wanted]

    async def get_latest_graph_run(self, graph_id: str):
        runs = [r for r in self.graph_runs.values() if r["graph_id"] == graph_id]
        return max(runs, key=lambda r: r.get("started_at") or NOW) if runs else None

    async def list_graph_runs(self, graph_id: str):
        return [r for r in self.graph_runs.values() if r["graph_id"] == graph_id]

    async def list_graph_runs_by_state(self, states, *, limit=200):
        return [r for r in self.graph_runs.values() if r["state"] in states]

    async def get_graph_run(self, run_id: str):
        return self.graph_runs.get(run_id)

    async def list_task_runs(self, graph_run_id: str):
        return [r for r in self.task_runs if r["graph_run_id"] == graph_run_id]

    async def list_task_runs_for_graph(self, graph_id: str):
        run_ids = {
            r["id"] for r in self.graph_runs.values() if r["graph_id"] == graph_id
        }
        return [r for r in self.task_runs if r["graph_run_id"] in run_ids]

    async def list_node_runs(self, graph_run_id: str):
        latest: dict[str, dict[str, Any]] = {}
        for run in self.task_runs:
            if run["graph_run_id"] != graph_run_id:
                continue
            task_id = run["task_id"]
            if task_id not in latest or run["attempt"] >= latest[task_id]["attempt"]:
                latest[task_id] = run
        return list(latest.values())


def make_client(repo: FakeRepository, user: dict[str, Any]):
    app = FastAPI()
    app.include_router(orch_router.router, prefix="/api/v1/task-orchestration")
    app.dependency_overrides[orch_router.get_current_user] = lambda: user
    orch_router._repository = repo  # type: ignore[assignment]
    return TestClient(app, raise_server_exceptions=False)


def alice() -> dict[str, Any]:
    return {"username": "alice", "roles": [], "session_id": "s", "encrypted_password": "e"}


def bob() -> dict[str, Any]:
    return {"username": "bob", "roles": [], "session_id": "s", "encrypted_password": "e"}


def admin() -> dict[str, Any]:
    return {
        "username": "root",
        "roles": ["ACCOUNTADMIN"],
        "session_id": "s",
        "encrypted_password": "e",
    }


@pytest.fixture(autouse=True)
def restore_repository(monkeypatch):
    """Restore the module-level repository after each test.

    The router binds the repository at import time; the fake is substituted on
    the module so every route sees it, and the real singleton is put back.
    """
    from app.modules.task_orchestration.repository import (
        task_orchestration_repository as real,
    )

    monkeypatch.setattr(orch_router, "_repository", real)
    yield


def graph_with_finalizer(repo: FakeRepository) -> None:
    repo.add_task("a", schedule_kind="cron", schedule_expr="0 2 * * *")
    repo.add_task("b")
    repo.add_task("f", when_expr="flag = TRUE")
    repo.add_edge("g1", "a", "b")
    repo.add_edge("g1", "a", "f", kind="finalize")
    repo.add_graph_run("run1", "g1", state="success", overlap_policy="queue")
    repo.add_task_run("tr_a", "run1", "id_a", state="success")
    repo.add_task_run("tr_b", "run1", "id_b", state="failed", error_message="boom")
    repo.add_task_run("tr_f", "run1", "id_f", state="skipped")


class TestGraphList:
    def test_lists_a_graph_with_its_root_schedule_and_last_run(self) -> None:
        repo = FakeRepository()
        graph_with_finalizer(repo)
        client = make_client(repo, alice())

        body = client.get("/api/v1/task-orchestration/graphs").json()

        assert body["count"] == 1
        graph = body["graphs"][0]
        assert graph["graph_id"] == "g1"
        assert graph["root_task"] == "a"
        assert graph["node_count"] == 3
        assert graph["schedule_kind"] == "cron"
        assert graph["schedule_expr"] == "0 2 * * *"
        # The root task's overlap policy, reported even though the graph run's
        # own policy is separate (asserted on last_run below).
        assert graph["overlap_policy"] == "skip"
        assert graph["last_run"]["id"] == "run1"
        assert graph["last_run"]["state"] == "success"
        assert graph["last_run"]["overlap_policy"] == "queue"

    def test_empty_state_returns_an_empty_list_not_an_error(self) -> None:
        client = make_client(FakeRepository(), alice())
        response = client.get("/api/v1/task-orchestration/graphs")
        assert response.status_code == 200
        assert response.json() == {"graphs": [], "count": 0}


class TestGraphDetail:
    def test_reports_edges_with_kind_and_the_finalizer_node(self) -> None:
        repo = FakeRepository()
        graph_with_finalizer(repo)
        client = make_client(repo, alice())

        body = client.get("/api/v1/task-orchestration/graphs/g1").json()

        assert body["node_count"] == 3
        kinds = {(e["parent_task"], e["child_task"]): e["edge_kind"] for e in body["edges"]}
        assert kinds[("a", "b")] == "after"
        assert kinds[("a", "f")] == "finalize"

        finalizer = next(n for n in body["nodes"] if n["name"] == "f")
        assert finalizer["is_finalizer"] is True
        assert finalizer["when_expr"] == "flag = TRUE"
        assert finalizer["last_state"] == "skipped"
        # Every node is present, not just the dependency graph.
        assert {n["name"] for n in body["nodes"]} == {"a", "b", "f"}

    def test_last_state_is_the_latest_attempt(self) -> None:
        repo = FakeRepository()
        repo.add_task("a")
        repo.add_task("b")
        repo.add_edge("g1", "a", "b")
        repo.add_graph_run("run1", "g1")
        repo.add_task_run("tr1", "run1", "id_a", state="failed", attempt=1)
        repo.add_task_run("tr2", "run1", "id_a", state="success", attempt=2)
        client = make_client(repo, alice())

        body = client.get("/api/v1/task-orchestration/graphs/g1").json()
        node_a = next(n for n in body["nodes"] if n["name"] == "a")
        assert node_a["last_state"] == "success"

    def test_unknown_graph_is_404_not_500(self) -> None:
        client = make_client(FakeRepository(), alice())
        response = client.get("/api/v1/task-orchestration/graphs/nope")
        assert response.status_code == 404


class TestOwnershipScoping:
    """The system-pool read bypasses StarRocks RBAC, so the API scopes by owner."""

    def test_a_user_does_not_see_another_users_graph(self) -> None:
        repo = FakeRepository()
        graph_with_finalizer(repo)  # owned by alice
        client = make_client(repo, bob())

        assert client.get("/api/v1/task-orchestration/graphs").json()["count"] == 0
        assert (
            client.get("/api/v1/task-orchestration/graphs/g1").status_code == 404
        ), "an unauthorized graph must 404, not reveal that it exists"

    def test_the_owner_sees_their_graph(self) -> None:
        repo = FakeRepository()
        graph_with_finalizer(repo)
        client = make_client(repo, alice())
        assert client.get("/api/v1/task-orchestration/graphs/g1").status_code == 200

    def test_an_admin_sees_every_graph(self) -> None:
        repo = FakeRepository()
        graph_with_finalizer(repo)
        client = make_client(repo, admin())
        assert client.get("/api/v1/task-orchestration/graphs").json()["count"] == 1
        assert client.get("/api/v1/task-orchestration/graphs/g1").status_code == 200

    def test_a_run_cannot_be_enumerated_across_owners(self) -> None:
        repo = FakeRepository()
        graph_with_finalizer(repo)
        client = make_client(repo, bob())
        # bob knows the run id but does not own the graph.
        assert client.get("/api/v1/task-orchestration/runs/run1").status_code == 404


class TestRunEndpoints:
    def test_graph_run_history_includes_policy_and_trigger(self) -> None:
        repo = FakeRepository()
        graph_with_finalizer(repo)
        client = make_client(repo, alice())

        body = client.get("/api/v1/task-orchestration/graphs/g1/runs").json()
        assert body["count"] == 1
        run = body["runs"][0]
        assert run["id"] == "run1"
        assert run["overlap_policy"] == "queue"
        assert run["trigger_type"] == "schedule"

    def test_single_run_returns_its_node_runs(self) -> None:
        repo = FakeRepository()
        graph_with_finalizer(repo)
        client = make_client(repo, alice())

        body = client.get("/api/v1/task-orchestration/runs/run1").json()
        assert body["run"]["id"] == "run1"
        states = {n["task_id"]: n["state"] for n in body["node_runs"]}
        assert states == {"id_a": "success", "id_b": "failed", "id_f": "skipped"}


class TestCredentialInvisibility:
    def test_task_body_is_not_exposed(self) -> None:
        repo = FakeRepository()
        graph_with_finalizer(repo)
        client = make_client(repo, alice())
        body = client.get("/api/v1/task-orchestration/graphs/g1").json()
        serialized = str(body).lower()
        assert "insert into" not in serialized, "the task body must not be returned"

    def test_node_error_message_is_redacted(self) -> None:
        repo = FakeRepository()
        repo.add_task("a")
        repo.add_graph_run("run1", "id_a")
        repo.add_task_run(
            "tr_a",
            "run1",
            "id_a",
            state="failed",
            error_message=(
                "engine error: FILES('path'='s3://b/x.csv','aws.s3.access_key'='AKIAEXAMPLE')"
            ),
        )
        client = make_client(repo, alice())
        body = client.get("/api/v1/task-orchestration/runs/run1").json()
        message = body["node_runs"][0]["error_message"]
        assert "AKIAEXAMPLE" not in message
        assert "***" in message

    def test_no_response_carries_a_credential_shaped_value(self) -> None:
        repo = FakeRepository()
        graph_with_finalizer(repo)
        client = make_client(repo, alice())
        for path in (
            "/api/v1/task-orchestration/graphs",
            "/api/v1/task-orchestration/graphs/g1",
            "/api/v1/task-orchestration/graphs/g1/runs",
            "/api/v1/task-orchestration/runs/run1",
        ):
            serialized = str(client.get(path).json()).lower()
            for bad in ("password", "secret", "token", "credential", "access_key"):
                assert bad not in serialized, f"{bad} in {path}"


class TestReadOnly:
    def test_no_mutating_methods_are_exposed(self) -> None:
        methods = set()
        for route in orch_router.router.routes:
            methods.update(getattr(route, "methods", set()))
        assert methods == {"GET"}, f"the orchestration API must be read-only, got {methods}"
