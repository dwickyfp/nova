"""Recover Studio learning from source-message state through the task journal."""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import ValidationError

from app.core.redis import session_store
from app.modules.assistant.repository import assistant_repository
from app.modules.intelligence.contracts import Scope, fingerprint
from app.modules.intelligence.engine_repository import metadata_lock
from app.modules.task_orchestration.internal_handlers import InternalTaskConfiguration
from app.modules.task_orchestration.repository import task_orchestration_repository
from app.modules.task_orchestration.transport import RedisGraphRunTransport


async def enqueue_pending_learning(
    *,
    repository=task_orchestration_repository,
    user: dict | None = None,
    agent_id: str | None = None,
) -> int:
    queued = 0
    client = session_store._redis
    cursor_key = "nova:intelligence:learning-admission-cursor"
    after = await client.get(cursor_key) if client is not None and user is None else ""
    if isinstance(after, bytes):
        after = after.decode()
    sources = await assistant_repository.pending_learning(
        limit=100,
        after=after or "",
        user_name=user["username"] if user else None,
        agent_id=agent_id,
    )
    if not sources and after and client is not None:
        await client.delete(cursor_key)
    for source in sources:
        if client is not None and user is None:
            await client.setex(cursor_key, 3600, source["message_id"])
        try:
            scope = Scope.model_validate(source["scope"])
        except (ValidationError, TypeError):
            continue
        if user and (
            scope != Scope.from_user(user) or (agent_id and source["agent_id"] != agent_id)
        ):
            continue
        if not scope.session_id:
            continue
        session = await session_store.get(scope.session_id)
        if not session:
            continue
        try:
            if Scope.from_user({**session, "session_id": scope.session_id}) != scope:
                continue
        except HTTPException:
            continue
        identity = fingerprint([source["message_id"], scope.model_dump()])
        name = f"intelligence_learning_{identity[:32]}"
        async with metadata_lock(f"learning-admission:{identity}"):
            task = await repository.find_task(name, None, None)
            if task is None:
                config = InternalTaskConfiguration(
                    scope=scope,
                    source_thread_id=source["thread_id"],
                    source_message_id=source["message_id"],
                    agent_id=source["agent_id"],
                )
                task = await repository.create_task(
                    {
                        "name": name,
                        "definition": "",
                        "schedule_kind": "manual",
                        "timezone": "UTC",
                        "owner_role": scope.active_role,
                        "overlap_policy": "skip",
                        "handler": "intelligence.consolidate",
                        "handler_config": config.model_dump(mode="json"),
                    },
                    scope.principal,
                )
            active = await repository.list_active_graph_runs(name)
            if active:
                continue
            # At most one retry admission per quarter hour; the source remains
            # pending after provider failure instead of disappearing with a coroutine.
            bucket = int(datetime.now(UTC).timestamp()) // 900
            run_id = fingerprint([identity, bucket])
            previous = await repository.get_graph_run(run_id)
            if previous is not None:
                continue
            run = await repository.create_graph_run(
                {
                    "id": run_id,
                    "graph_id": name,
                    "trigger_type": "manual",
                    "state": "pending",
                    "overlap_policy": "skip",
                    "execution_user": scope.principal,
                    "execution_role": scope.active_role,
                    "execution_session_id": scope.session_id,
                }
            )
        queued += 1
        if session_store._redis is not None:
            # A missed wakeup is recovered from the durable pending graph run.
            with suppress(Exception):
                await RedisGraphRunTransport(session_store._redis).publish_graph_run(
                    run, [task["id"]]
                )
        if queued >= 6:
            break
    return queued
