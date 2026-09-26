"""Unit tests for orchestration reads and manual runs.

The endpoints read `CONFIG_TASK*` through the repository, which is replaced here
with an in-memory fake, and the authenticated user is supplied through a
dependency override. No engine, no Redis.

These pin the contract the UI depends on, the ownership scoping the system-pool
read would otherwise bypass, and the credential-invisibility rules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.task_orchestration import router as orch_router

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def engine_role_check(monkeypatch):
    check = AsyncMock()
    monkeypatch.setattr(orch_router, "verify_active_role", check)
    return check


def _qualified(task: dict[str, Any]) -> str:
    parts = [
        str(part)
        for part in (task.get("database_name"), task.get("schema_name"), task.get("name"))
        if part
    ]
    return ".".join(parts)


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
        database_name: str = "db1",
        schema_name: str = "default",
    ) -> str:
        task_id = f"id_{name}"
        self.tasks[name] = {
            "id": task_id,
            "name": name,
            "definition": "INSERT INTO t SELECT 1",
            "database_name": database_name,
            "schema_name": schema_name,
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
            str(endpoint) for e in self.edges for endpoint in (e["parent_task"], e["child_task"])
        }
        # A standalone task's graph id is its qualified name, matching the repo.
        standalone = sorted(
            _qualified(t) for t in self.tasks.values() if str(t["name"]) not in referenced
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

    async def count_graph_runs(self, graph_id: str) -> int:
        return len([r for r in self.graph_runs.values() if r["graph_id"] == graph_id])

    async def list_graph_runs_page(self, graph_id: str, *, limit: int, offset: int):
        runs = sorted(
            (r for r in self.graph_runs.values() if r["graph_id"] == graph_id),
            key=lambda r: (r.get("started_at") or NOW, r["id"]),
            reverse=True,
        )
        return runs[offset : offset + limit]

    async def count_graph_runs_by_graph(self) -> dict[str, dict[str, int]]:
        tallies: dict[str, dict[str, int]] = {}
        for run in self.graph_runs.values():
            tally = tallies.setdefault(
                str(run["graph_id"]), {"total": 0, "success": 0, "failed": 0}
            )
            tally["total"] += 1
            if run["state"] == "success":
                tally["success"] += 1
            elif run["state"] == "failed":
                tally["failed"] += 1
        return tallies

    async def list_graph_runs_by_state(self, states, *, limit=200):
        return [r for r in self.graph_runs.values() if r["state"] in states]

    async def get_graph_run(self, run_id: str):
        return self.graph_runs.get(run_id)

    async def list_task_runs(self, graph_run_id: str):
        return [r for r in self.task_runs if r["graph_run_id"] == graph_run_id]

    async def list_task_runs_for_graph(self, graph_id: str):
        run_ids = {r["id"] for r in self.graph_runs.values() if r["graph_id"] == graph_id}
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
    return {
        "username": "alice",
        "roles": ["analyst"],
        "active_role": "analyst",
        "session_id": "s",
        "encrypted_password": "e",
    }


def bob() -> dict[str, Any]:
    return {"username": "bob", "roles": [], "session_id": "s", "encrypted_password": "e"}


def admin() -> dict[str, Any]:
    return {
        "username": "root",
        "roles": ["ACCOUNTADMIN"],
        "active_role": "ACCOUNTADMIN",
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
    repo.add_edge("db1.default.g1", "a", "b")
    repo.add_edge("db1.default.g1", "a", "f", kind="finalize")
    repo.add_graph_run("run1", "db1.default.g1", state="success", overlap_policy="queue")
    repo.add_task_run("tr_a", "run1", "id_a", state="success")
    repo.add_task_run("tr_b", "run1", "id_b", state="failed", error_message="boom")
    repo.add_task_run("tr_f", "run1", "id_f", state="skipped")


def mixed_ownership_graph(repo: FakeRepository) -> None:
    """A reachable graph whose nodes belong to two different users.

    `CREATE TASK x AFTER a` does not check who owns `a`
    (`lowering.persist_lowered_task`), so this shape is producible in
    production: alice owns `m_a`, bob owns `m_b`, and one edge joins them.
    """
    repo.add_task("m_a", owner="alice")
    repo.add_task("m_b", owner="bob")
    repo.add_edge("db1.default.g_mixed", "m_a", "m_b")


class TestGraphList:
    def test_lists_a_graph_with_its_root_schedule_and_last_run(self) -> None:
        repo = FakeRepository()
        graph_with_finalizer(repo)
        client = make_client(repo, alice())

        body = client.get("/api/v1/task-orchestration/graphs").json()

        assert body["count"] == 1
        graph = body["graphs"][0]
        assert graph["graph_id"] == "db1.default.g1"
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

        body = client.get("/api/v1/task-orchestration/graphs/db1.default.g1").json()

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
        repo.add_edge("db1.default.g1", "a", "b")
        repo.add_graph_run("run1", "db1.default.g1")
        repo.add_task_run("tr1", "run1", "id_a", state="failed", attempt=1)
        repo.add_task_run("tr2", "run1", "id_a", state="success", attempt=2)
        client = make_client(repo, alice())

        body = client.get("/api/v1/task-orchestration/graphs/db1.default.g1").json()
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
        assert client.get("/api/v1/task-orchestration/graphs/db1.default.g1").status_code == 404, (
            "an unauthorized graph must 404, not reveal that it exists"
        )

    def test_the_owner_sees_their_graph(self) -> None:
        repo = FakeRepository()
        graph_with_finalizer(repo)
        client = make_client(repo, alice())
        assert client.get("/api/v1/task-orchestration/graphs/db1.default.g1").status_code == 200

    def test_an_admin_sees_every_graph(self) -> None:
        repo = FakeRepository()
        graph_with_finalizer(repo)
        client = make_client(repo, admin())
        assert client.get("/api/v1/task-orchestration/graphs").json()["count"] == 1
        assert client.get("/api/v1/task-orchestration/graphs/db1.default.g1").status_code == 200

    def test_a_run_cannot_be_enumerated_across_owners(self) -> None:
        repo = FakeRepository()
        graph_with_finalizer(repo)
        client = make_client(repo, bob())
        # bob knows the run id but does not own the graph.
        assert client.get("/api/v1/task-orchestration/runs/run1").status_code == 404


class TestMixedOwnershipIsFailClosed:
    """`all(...)`, not `any(...)`: one unowned node hides the whole graph.

    This pins the rule that the module docstring and the user docs describe. It
    is the test whose absence let the docs drift from the code: with `any`, both
    owners below would read the graph (and each other's task definitions).
    """

    def test_neither_non_admin_owner_sees_the_mixed_graph(self) -> None:
        repo = FakeRepository()
        mixed_ownership_graph(repo)

        alice_client = make_client(repo, alice())  # owns m_a only
        bob_client = make_client(repo, bob())  # owns m_b only

        for who, client in (("alice", alice_client), ("bob", bob_client)):
            listed = client.get("/api/v1/task-orchestration/graphs").json()
            assert listed["count"] == 0, f"{who} must not list a mixed-ownership graph"
            detail = client.get("/api/v1/task-orchestration/graphs/db1.default.g_mixed")
            assert detail.status_code == 404, f"{who} must get 404, not the graph"

    def test_an_admin_sees_the_mixed_graph(self) -> None:
        repo = FakeRepository()
        mixed_ownership_graph(repo)
        client = make_client(repo, admin())

        assert client.get("/api/v1/task-orchestration/graphs").json()["count"] == 1
        body = client.get("/api/v1/task-orchestration/graphs/db1.default.g_mixed").json()
        assert {n["name"] for n in body["nodes"]} == {"m_a", "m_b"}

    def test_each_node_is_owned_by_exactly_one_single_owner(self) -> None:
        """The precondition that makes the two `any`-based leaks possible.

        alice owns one node and bob owns the other, and neither holds an admin
        role — so under an `any` rule each of them would see the graph. That is
        what this class exists to prevent.
        """
        repo = FakeRepository()
        mixed_ownership_graph(repo)

        owners = {name: row["created_by"] for name, row in repo.tasks.items()}
        assert owners == {"m_a": "alice", "m_b": "bob"}
        assert not orch_router._is_admin(alice())
        assert not orch_router._is_admin(bob())


class TestRunEndpoints:
    def test_graph_run_history_includes_policy_and_trigger(self) -> None:
        repo = FakeRepository()
        graph_with_finalizer(repo)
        client = make_client(repo, alice())

        body = client.get("/api/v1/task-orchestration/graphs/db1.default.g1/runs").json()
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


class TestRunCounts:
    """`GET /graphs` carries run tallies so the task list needs one call."""

    def test_counts_split_success_failed_and_total(self) -> None:
        repo = FakeRepository()
        repo.add_task("a")
        repo.add_graph_run("r1", "db1.default.a", state="success")
        repo.add_graph_run("r2", "db1.default.a", state="failed")
        repo.add_graph_run("r3", "db1.default.a", state="success")
        client = make_client(repo, alice())

        graph = client.get("/api/v1/task-orchestration/graphs").json()["graphs"][0]
        assert graph["run_counts"] == {"total": 3, "success": 2, "failed": 1}

    def test_cancelled_is_counted_in_total_but_not_success_or_failed(self) -> None:
        repo = FakeRepository()
        repo.add_task("a")
        repo.add_graph_run("r1", "db1.default.a", state="cancelled")
        client = make_client(repo, alice())

        graph = client.get("/api/v1/task-orchestration/graphs").json()["graphs"][0]
        assert graph["run_counts"] == {"total": 1, "success": 0, "failed": 0}

    def test_a_graph_with_no_runs_reports_zeroes(self) -> None:
        repo = FakeRepository()
        repo.add_task("a")
        client = make_client(repo, alice())

        graph = client.get("/api/v1/task-orchestration/graphs").json()["graphs"][0]
        assert graph["run_counts"] == {"total": 0, "success": 0, "failed": 0}


class TestRunPagination:
    def test_limit_and_offset_slice_the_history_and_count_is_unpaginated(self) -> None:
        repo = FakeRepository()
        repo.add_task("a")
        for index in range(5):
            repo.add_graph_run(
                f"r{index}",
                "db1.default.a",
                started_at=datetime(2026, 1, 1, 12, index, tzinfo=UTC),
            )
        client = make_client(repo, alice())

        body = client.get(
            "/api/v1/task-orchestration/graphs/db1.default.a/runs?limit=2&offset=0"
        ).json()
        assert body["count"] == 5, "count is the full history, not the page"
        assert [r["id"] for r in body["runs"]] == ["r4", "r3"]

        page2 = client.get(
            "/api/v1/task-orchestration/graphs/db1.default.a/runs?limit=2&offset=2"
        ).json()
        assert [r["id"] for r in page2["runs"]] == ["r2", "r1"]

    def test_rejects_a_nonpositive_limit(self) -> None:
        repo = FakeRepository()
        repo.add_task("a")
        client = make_client(repo, alice())
        response = client.get("/api/v1/task-orchestration/graphs/db1.default.a/runs?limit=0")
        assert response.status_code == 422


class TestStandaloneGraph:
    """A task with no edges is still a one-node graph.

    This is the regression guard for the reported defect: a standalone task
    listed a graph id but its detail answered `0 nodes`, because the detail
    lookup did not resolve the task row behind the qualified graph id.
    """

    def test_a_standalone_task_is_a_single_node_graph(self) -> None:
        repo = FakeRepository()
        repo.add_task("solo")
        client = make_client(repo, alice())

        listed = client.get("/api/v1/task-orchestration/graphs").json()
        assert listed["count"] == 1
        graph_id = listed["graphs"][0]["graph_id"]
        assert graph_id == "db1.default.solo"

        detail = client.get(f"/api/v1/task-orchestration/graphs/{graph_id}").json()
        assert detail["node_count"] == 1
        assert [n["name"] for n in detail["nodes"]] == ["solo"]
        assert detail["nodes"][0]["last_state"] is None

    def test_a_standalone_task_that_has_run_reports_its_state(self) -> None:
        repo = FakeRepository()
        repo.add_task("solo")
        repo.add_graph_run("run1", "db1.default.solo", state="success")
        repo.add_task_run("tr1", "run1", "id_solo", state="success")
        client = make_client(repo, alice())

        detail = client.get("/api/v1/task-orchestration/graphs/db1.default.solo").json()
        assert detail["node_count"] == 1
        assert detail["nodes"][0]["last_state"] == "success"

    def test_a_chain_adds_nodes_to_the_same_graph(self) -> None:
        repo = FakeRepository()
        repo.add_task("a")
        repo.add_task("b")
        repo.add_edge("db1.default.g1", "a", "b")
        client = make_client(repo, alice())

        detail = client.get("/api/v1/task-orchestration/graphs/db1.default.g1").json()
        assert detail["node_count"] == 2
        assert {n["name"] for n in detail["nodes"]} == {"a", "b"}

    def test_an_edge_whose_task_row_is_gone_does_not_list_an_empty_graph(self) -> None:
        # The data can hold an orphan edge: a task row dropped while its edge
        # remained. It must not surface as a graph that renders zero nodes.
        repo = FakeRepository()
        repo.add_edge("db1.default.ghost", "gone_parent", "gone_child")
        client = make_client(repo, alice())

        listed = client.get("/api/v1/task-orchestration/graphs").json()
        assert listed["count"] == 0
        assert client.get("/api/v1/task-orchestration/graphs/db1.default.ghost").status_code == 404

    def test_an_admin_does_not_see_an_orphan_graph_either(self) -> None:
        # "Admin sees every graph" means every graph that exists, not every
        # dangling edge: an empty flow is a bug, not a privilege.
        repo = FakeRepository()
        repo.add_edge("db1.default.ghost", "gone_parent", "gone_child")
        client = make_client(repo, admin())

        assert client.get("/api/v1/task-orchestration/graphs").json()["count"] == 0

    def test_a_task_row_without_a_schema_still_resolves_from_a_scoped_graph_id(
        self,
    ) -> None:
        # A legacy row has schema_name NULL while the edge's graph id carries
        # both parts. The bare-name fallback must still find it, or the flow
        # renders empty.
        repo = FakeRepository()
        repo.add_task("a", schema_name=None)
        repo.add_task("b", schema_name=None)
        repo.add_edge("db1.default.g1", "a", "b")
        client = make_client(repo, alice())

        detail = client.get("/api/v1/task-orchestration/graphs/db1.default.g1").json()
        assert detail["node_count"] == 2
        assert {n["name"] for n in detail["nodes"]} == {"a", "b"}


class TestCredentialInvisibility:
    def test_task_body_is_not_exposed(self) -> None:
        repo = FakeRepository()
        graph_with_finalizer(repo)
        client = make_client(repo, alice())
        body = client.get("/api/v1/task-orchestration/graphs/db1.default.g1").json()
        serialized = str(body).lower()
        assert "insert into" not in serialized, "the task body must not be returned"

    def test_node_error_message_is_redacted(self) -> None:
        repo = FakeRepository()
        repo.add_task("a")
        repo.add_graph_run("run1", "db1.default.a")
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
            "/api/v1/task-orchestration/graphs/db1.default.g1",
            "/api/v1/task-orchestration/graphs/db1.default.g1/runs",
            "/api/v1/task-orchestration/runs/run1",
        ):
            serialized = str(client.get(path).json()).lower()
            for bad in ("password", "secret", "token", "credential", "access_key"):
                assert bad not in serialized, f"{bad} in {path}"


class TestTaskSQL:
    def test_owner_can_read_original_body(self) -> None:
        repo = FakeRepository()
        task_id = repo.add_task("sql_task")
        body = "INSERT INTO destination SELECT id, amount FROM source"
        repo.tasks["sql_task"]["definition"] = body
        response = make_client(repo, alice()).get(
            f"/api/v1/task-orchestration/graphs/db1.default.sql_task/nodes/{task_id}/sql"
        )
        assert response.status_code == 200
        assert response.json()["sql"] == body

    @pytest.mark.parametrize(
        "user", [{"username": "outsider", "roles": []}, {"username": "", "roles": []}]
    )
    def test_unowned_sql_is_not_exposed(self, user) -> None:
        repo = FakeRepository()
        task_id = repo.add_task("sql_task")
        response = make_client(repo, user).get(
            f"/api/v1/task-orchestration/graphs/db1.default.sql_task/nodes/{task_id}/sql"
        )
        assert response.status_code == 404

    def test_node_must_belong_to_authorized_graph(self) -> None:
        repo = FakeRepository()
        repo.add_task("mine")
        other = repo.add_task("other", owner="bob")
        response = make_client(repo, alice()).get(
            f"/api/v1/task-orchestration/graphs/db1.default.mine/nodes/{other}/sql"
        )
        assert response.status_code == 404

    def test_legacy_credential_values_are_redacted(self) -> None:
        repo = FakeRepository()
        task_id = repo.add_task("sql_task")
        repo.tasks["sql_task"]["definition"] = (
            "INSERT INTO t SELECT * FROM FILES('aws.s3.secret_key'='private-value', 'format'='csv')"
        )
        response = make_client(repo, alice()).get(
            f"/api/v1/task-orchestration/graphs/db1.default.sql_task/nodes/{task_id}/sql"
        )
        assert response.status_code == 200
        assert "private-value" not in response.text
        assert "***" in response.json()["sql"]


class TestManualRuns:
    @pytest.mark.parametrize("role", [None, "unassigned"])
    def test_manual_run_requires_assigned_active_role(self, manual, role):
        repo, publish, _ = manual
        response = make_client(repo, {**alice(), "active_role": role}).post(
            "/api/v1/task-orchestration/graphs/db1.default.g1/runs"
        )
        assert response.status_code == 404
        repo.create_graph_run.assert_not_awaited()
        publish.assert_not_awaited()

    def test_admin_run_uses_caller_not_task_owner_or_request_body(self, manual):
        repo, _, _ = manual
        for task in repo.tasks.values():
            task["owner_role"] = "ACCOUNTADMIN"
        caller = {
            "username": "dwicky.f.putra",
            "session_id": "session-dwicky",
            "roles": ["ACCOUNTADMIN"],
            "active_role": "ACCOUNTADMIN",
        }
        response = make_client(repo, caller).post(
            "/api/v1/task-orchestration/graphs/db1.default.g1/runs",
            json={"execution_user": "root", "execution_role": "root"},
        )
        assert response.status_code == 202
        saved = repo.create_graph_run.call_args.args[0]
        assert saved["execution_user"] == "dwicky.f.putra"
        assert saved["execution_role"] == "ACCOUNTADMIN"

    @pytest.fixture
    def manual(self, monkeypatch):
        repo = FakeRepository()
        graph_with_finalizer(repo)
        for task in repo.tasks.values():
            task["owner_role"] = "analyst"
        repo.list_active_graph_runs = AsyncMock(return_value=[])
        repo.create_graph_run = AsyncMock(
            return_value={
                "id": "manual1",
                "graph_id": "db1.default.g1",
                "state": "pending",
                "trigger_type": "manual",
                "overlap_policy": "skip",
                "started_at": NOW,
            }
        )
        publish = AsyncMock()
        audit = AsyncMock()
        monkeypatch.setattr(orch_router, "_publish_manual_run", publish)
        monkeypatch.setattr(orch_router, "write_audit_log", audit)
        return repo, publish, audit

    def test_owner_queues_all_nodes_and_audits(self, manual):
        repo, publish, audit = manual
        response = make_client(repo, alice()).post(
            "/api/v1/task-orchestration/graphs/db1.default.g1/runs"
        )
        assert response.status_code == 202
        assert response.json()["trigger_type"] == "manual"
        repo.create_graph_run.assert_awaited_once_with(
            {
                "graph_id": "db1.default.g1",
                "trigger_type": "manual",
                "state": "pending",
                "overlap_policy": "skip",
                "execution_user": "alice",
                "execution_role": "analyst",
                "execution_session_id": "s",
            }
        )
        assert set(publish.call_args.args[1]) == {task["id"] for task in repo.tasks.values()}
        assert [call.kwargs["status"] for call in audit.call_args_list] == ["ATTEMPTED", "SUCCESS"]

    @pytest.mark.parametrize("graph_id", ["db1.default.g1", "missing"])
    def test_unknown_or_unowned_graph_cannot_run(self, manual, graph_id):
        repo, publish, audit = manual
        response = make_client(repo, {"username": "outsider", "roles": []}).post(
            f"/api/v1/task-orchestration/graphs/{graph_id}/runs"
        )
        assert response.status_code == 404
        repo.create_graph_run.assert_not_awaited()
        publish.assert_not_awaited()

    def test_skip_policy_rejects_active_run(self, manual):
        repo, publish, audit = manual
        repo.list_active_graph_runs.return_value = [{"id": "active"}]
        response = make_client(repo, alice()).post(
            "/api/v1/task-orchestration/graphs/db1.default.g1/runs"
        )
        assert response.status_code == 409
        repo.create_graph_run.assert_not_awaited()
        publish.assert_not_awaited()

    @pytest.mark.parametrize("policy", ["queue", "allow"])
    def test_other_overlap_policies_accept_active_run(self, manual, policy):
        repo, publish, audit = manual
        for task in repo.tasks.values():
            task["overlap_policy"] = policy
        repo.list_active_graph_runs.return_value = [{"id": "active"}]
        response = make_client(repo, alice()).post(
            "/api/v1/task-orchestration/graphs/db1.default.g1/runs"
        )
        assert response.status_code == 202
        assert repo.create_graph_run.call_args.args[0]["overlap_policy"] == policy

    def test_transport_failure_keeps_durable_run_accepted(self, manual):
        repo, publish, audit = manual
        publish.side_effect = RuntimeError("private connection details")
        response = make_client(repo, alice()).post(
            "/api/v1/task-orchestration/graphs/db1.default.g1/runs"
        )
        assert response.status_code == 202
        assert "private" not in response.text

    def test_persistence_failure_does_not_publish(self, manual):
        repo, publish, audit = manual
        repo.create_graph_run.side_effect = RuntimeError("private connection details")
        response = make_client(repo, alice()).post(
            "/api/v1/task-orchestration/graphs/db1.default.g1/runs"
        )
        assert response.status_code == 503
        assert "private" not in response.text
        publish.assert_not_awaited()

    def test_only_manual_run_mutation_is_exposed(self):
        mutations = [
            (route.path, method)
            for route in orch_router.router.routes
            for method in getattr(route, "methods", set())
            if method != "GET"
        ]
        assert mutations == [("/graphs/{graph_id}/runs", "POST")]
