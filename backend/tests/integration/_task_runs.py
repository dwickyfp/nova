"""Build lifecycle fixtures through the same claims and fencing used by workers."""


async def create_graph_run(repo, data):
    state = data.get("state", "pending")
    row = await repo.create_graph_run({**data, "state": "pending"})
    if state != "pending":
        generation = await repo.claim_graph_run(row["id"], ["pending"], "running")
        assert generation is not None
        if state != "running":
            assert await repo.transition_graph_run(
                row["id"],
                ["running"],
                state,
                generation=generation,
            )
    return await repo.get_graph_run(row["id"])


async def create_task_run(repo, data):
    prior = await repo.list_task_runs(data["graph_run_id"])
    attempt = data.get("attempt") or 1 + max(
        (row["attempt"] for row in prior if row["task_id"] == data["task_id"]),
        default=0,
    )
    row = await repo.create_task_run_once(
        data["graph_run_id"],
        data["task_id"],
        attempt,
        delegated=data.get("delegated", True),
    )
    state = data.get("state", "pending")
    if state != "pending":
        generation = await repo.claim_task_run(row["id"], ["pending"])
        assert generation is not None
        if state != "running":
            assert await repo.finish_task_run(
                row["id"],
                state,
                generation=generation,
                query_id=data.get("starrocks_query_id"),
                error_message=data.get("error_message"),
            )
    return await repo.get_task_run(row["id"])


async def latest_node(repo, graph_run_id, task_id):
    return next(
        (row for row in await repo.list_node_runs(graph_run_id) if row["task_id"] == task_id),
        None,
    )
