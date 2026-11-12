"""Retain an uncertain Task after its actual engine mutation committed."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.database import db
from app.modules.task_orchestration.dag import GraphState
from app.modules.task_orchestration.execution import NodeOutcomeUnknown, native_attempt_name
from app.modules.task_orchestration.worker import GraphRunJob, GraphRunWorker
from tests.integration._task_runs import latest_node
from tests.integration.test_task_worker import (
    FORBIDDEN_DB,
    RESTRICTED_ROLE,
    RESTRICTED_USER,
    _executor,
    _make_graph,
    repo,
)
from tests.integration.test_task_worker import cleanup_runs as cleanup_runs
from tests.integration.test_task_worker import task_metadata_login as task_metadata_login
from tests.integration.test_task_worker import worker_infra as worker_infra

pytestmark = pytest.mark.engine


async def test_lost_completion_after_commit_never_repeats_the_insert(
    worker_infra, cleanup_runs, monkeypatch, task_definitions
):
    suffix = uuid4().hex[:12]
    table = f"{FORBIDDEN_DB}.receipt_{suffix}"
    name = f"receipt_{suffix}"
    node = None
    await db.execute_system(
        f"CREATE TABLE {table} (id INT NOT NULL) DUPLICATE KEY(id) "
        'DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES("replication_num"="1")'
    )
    try:
        await db.execute_system(f"GRANT INSERT, SELECT ON {table} TO ROLE {RESTRICTED_ROLE}")
        run_id, ids = await _make_graph(
            task_definitions=task_definitions,
            suffix=suffix,
            owners={name: RESTRICTED_USER},
            bodies={name: f"INSERT INTO {table} SELECT 1"},
            edges=[],
        )
        cleanup_runs["graph"].append(run_id)
        cleanup_runs["task"].append(ids[name])
        executor = _executor()
        observe = executor._await_completion
        actual_query_ids = []

        async def lose_completion(*args, **kwargs):
            result = await observe(*args, **kwargs)
            assert result.state in {"FINISHED", "SUCCESS"}
            actual_query_ids.append(result.query_id)
            raise NodeOutcomeUnknown(query_id=result.query_id)

        submit = AsyncMock(wraps=executor._submit)
        monkeypatch.setattr(executor, "_submit", submit)
        monkeypatch.setattr(executor, "_await_completion", lose_completion)
        worker = GraphRunWorker(repo, executor)
        job = GraphRunJob(run_id, ids[name])
        for _ in range(3):
            assert await worker.handle(job) == GraphState.RUNNING
        node = await latest_node(repo, run_id, ids[name])
        assert node is not None and node["state"] == "abandoned"
        assert node["starrocks_query_id"] == actual_query_ids[0]
        assert actual_query_ids[0]
        assert submit.await_count == 1
        assert (await repo.get_graph_run(run_id))["state"] == "running"
        assert (await db.execute_system(f"SELECT COUNT(*) FROM {table}"))["rows"][0][0] == 1
    finally:
        if node:
            await db.execute_system(
                f"DROP TASK IF EXISTS {FORBIDDEN_DB}.{native_attempt_name(node['id'])}"
            )
        await db.execute_system(f"DROP TABLE {table}")
