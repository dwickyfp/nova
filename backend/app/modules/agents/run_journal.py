"""Scoped, durable SSE journal for an Agent Studio turn.

Runs and their events live in PostgreSQL (``nova_meta.config_agent_runs`` and
``config_agent_run_events``). A run starts with claim generation 1, bound to the
starting login and security-context version. The producer that started it keeps
that generation in this process and passes it with every append, heartbeat and
finish; a takeover or interruption advances the generation in PG, so an older
producer is fenced out by the database rather than by a Redis lock. An event
batch and the run's high-water mark commit in one transaction.
"""

from __future__ import annotations

import json
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from app.core.metadata_actor import actor_for, current_actor, metadata_transaction
from app.core.metadata_db import MetadataConflict
from app.modules.agents.metadata_repository import AgentMetadataRepository


def _frame_sequence(frame: str) -> int:
    try:
        payload = json.loads(frame.split("data: ", 1)[1])
        return int(payload["sequence"])
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("The run event has no sequence.") from exc


def _naive(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


@asynccontextmanager
async def _store(
    owner_name: str | None = None, *, read_only: bool = False,
) -> AsyncIterator[AgentMetadataRepository]:
    actor = actor_for(owner_name) if owner_name is not None else current_actor()
    async with metadata_transaction(actor, read_only=read_only) as (connection, actor):
        yield AgentMetadataRepository(connection, actor)


class AgentRunJournal:
    def __init__(self) -> None:
        # run_id -> claim generation this process holds as the run's producer.
        self._claims: dict[str, int] = {}
        # Runs whose caller switched role mid-turn; marked when the run closes.
        self._role_changed: set[str] = set()
        self._lock = threading.Lock()

    async def ensure_schema(self) -> None:
        """PostgreSQL migrations own this schema; nothing is created at startup."""

    def claim_of(self, run_id: str) -> int | None:
        with self._lock:
            return self._claims.get(run_id)

    def _claim(self, run_id: str, generation: int | None) -> int:
        held = generation if generation is not None else self.claim_of(run_id)
        if held is None:
            raise ValueError("This process does not hold the run's claim")
        return held

    def _release(self, run_id: str) -> bool:
        with self._lock:
            self._claims.pop(run_id, None)
            changed = run_id in self._role_changed
            self._role_changed.discard(run_id)
        return changed

    async def start(
        self, *, run_id: str, owner_name: str, agent_id: str,
        thread_id: str, role_name: str, session_id: str | None = None,
        security_version: int | None = None,
    ) -> int:
        """Open the run as its producer; returns the claim generation it holds.

        The run binds to the actor's login, active role and security version;
        the arguments must describe that same actor.
        """
        async with _store(owner_name) as store:
            actor = store.actor
            if (role_name != actor.active_role
                    or (session_id is not None and session_id != actor.session_id)
                    or (security_version is not None
                        and security_version != actor.security_context_version)):
                raise ValueError("A run starts in its caller's own security context")
            row = await store.start_run(run_id, agent_id, thread_id)
        generation = int(row["claim_generation"])
        with self._lock:
            self._claims[run_id] = generation
        return generation

    async def append(self, run_id: str, frame: str, *, generation: int | None = None) -> int:
        return await self.append_batch(run_id, [frame], generation=generation)

    async def append_batch(
        self, run_id: str, frames: list[str], *, generation: int | None = None,
    ) -> int:
        if not frames or len(frames) > 32:
            raise ValueError("A run event batch must contain 1–32 frames.")
        sequences = [_frame_sequence(frame) for frame in frames]
        if any(
            current <= previous
            for previous, current in zip(sequences, sequences[1:], strict=False)
        ):
            raise ValueError("Run event sequence is not increasing.")
        claim = self._claim(run_id, generation)
        async with _store() as store:
            return await store.append_events(run_id, frames, generation=claim)

    async def finish(self, run_id: str, status: str, *, generation: int | None = None) -> None:
        """Close the run.

        The producer closes under its claim. Without a claim (a Mission stopping
        a run it does not produce) only ``interrupted`` is allowed: that fences
        the producer out by advancing the claim.
        """
        if status not in {"completed", "failed", "interrupted", "cancelled"}:
            raise ValueError("Invalid run status")
        claim = generation if generation is not None else self.claim_of(run_id)
        with self._lock:
            role_changed = run_id in self._role_changed
        async with _store() as store:
            if claim is None:
                if status != "interrupted":
                    raise ValueError("Only the run's producer can finish it")
                await store.interrupt_stale(run_id, stale_after_seconds=0)
                return
            if not await store.finish_run(run_id, status, generation=claim):
                raise MetadataConflict()
            if role_changed:
                await store.block_replay_after_role_change(run_id)
        # A lost commit acknowledgment leaves this claim available for verification.
        self._release(run_id)

    async def heartbeat(self, run_id: str, *, generation: int | None = None) -> bool:
        claim = self._claim(run_id, generation)
        async with _store() as store:
            return await store.heartbeat_run(run_id, generation=claim)

    async def interrupt_stale(
        self, run_id: str, *, owner_name: str, agent_id: str,
        thread_id: str, role_name: str, stale_after_seconds: int = 60,
    ) -> bool:
        """Close a run whose producer has stopped renewing its lease."""
        async with _store(owner_name) as store:
            run = await store.get_run(run_id)
            if not self._matches(run, agent_id=agent_id, thread_id=thread_id,
                                 role_name=role_name, actor_role=store.actor.active_role):
                return False
            assert run is not None
            if run["status"] != "running":
                return bool(run["status"] == "interrupted")
            return await store.interrupt_stale(run_id, stale_after_seconds=stale_after_seconds)

    async def block_replay_after_role_change(self, run_id: str) -> None:
        """Mark that the caller's role changed; applied when the producer closes the run.

        Event appends are bound to the role the run started in, so the marker is
        written together with the final status instead of fencing the producer
        out before it can record why it stopped.
        """
        with self._lock:
            self._role_changed.add(run_id)

    @staticmethod
    def _matches(run: dict[str, Any] | None, *, agent_id: str, thread_id: str,
                 role_name: str, actor_role: str) -> bool:
        return (run is not None and run["agent_id"] == agent_id
                and run["thread_id"] == thread_id and run["role_name"] == role_name
                and role_name == actor_role)

    async def get(
        self, run_id: str, *, owner_name: str, agent_id: str,
        thread_id: str, role_name: str,
    ) -> dict[str, Any] | None:
        async with _store(owner_name, read_only=True) as store:
            run = await store.get_run(run_id)
            actor_role = store.actor.active_role
        if not self._matches(run, agent_id=agent_id, thread_id=thread_id,
                             role_name=role_name, actor_role=actor_role):
            return None
        assert run is not None
        return {"run_id": run["run_id"], "status": run["status"],
                "last_sequence": int(run["last_sequence"]),
                "updated_at": _naive(run["updated_at"])}

    async def current(
        self, *, owner_name: str, agent_id: str, thread_id: str,
        role_name: str, security_version: int,
    ) -> str | None:
        async with _store(owner_name, read_only=True) as store:
            actor = store.actor
            if role_name != actor.active_role or security_version != actor.security_context_version:
                return None
            run = await store.current_run(agent_id, thread_id)
        return str(run["run_id"]) if run else None

    async def has_security_version(self, run_id: str, security_version: int) -> bool:
        async with _store(read_only=True) as store:
            run = await store.get_run(run_id)
        return run is not None and run["security_version"] == security_version

    async def events_after(self, run_id: str, sequence: int, *, limit: int = 100) -> list[str]:
        """Stored frames after ``sequence`` up to the run's high-water mark.

        Events a historical run never wrote are not waited for or invented.
        """
        async with _store(read_only=True) as store:
            history = await store.history(run_id, sequence, limit=limit)
        return [str(frame) for frame in history["frames"]]

    async def delete_thread(self, thread_id: str, *, owner_name: str) -> None:
        async with _store(owner_name) as store:
            await store.delete_thread_runs(thread_id)


run_journal = AgentRunJournal()
