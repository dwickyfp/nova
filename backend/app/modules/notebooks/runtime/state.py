"""Live notebook runtime state in Redis.

Redis owns runtime sessions, queues, leases and events (root AGENTS.md); the
durable record of executions and runtime lifecycles is in ``NOVA_SYSTEM``.
Every key here expires on its own.

* ``nova:nb:rt:<runtime>``      hash: the live runtime. Its existence is what a
  notebook data token is bound to, so deleting it revokes Nova data access.
* ``nova:nb:active:<notebook>`` the notebook's live runtime id.
* ``nova:nb:last:<notebook>``   hash: how the last runtime ended.
* ``nova:nb:runtimes``          set of live runtime ids (quotas, sweeps).
* ``nova:nb:q:<runtime>``       list of queued job ids (FIFO).
* ``nova:nb:job:<job>``         hash: one job (start, execute, restart, …).
* ``nova:nb:lease:<runtime>``   the worker draining the runtime's queue.
* ``nova:nb:exec:<execution>``  hash: an execution while queued or running.
* ``nova:nb:inflight:<notebook>`` hash: cell id → execution id not yet finished.
* ``nova:nb:cancel:<execution>`` flag: cancel this execution.
* ``nova:nb:ev:<notebook>``     stream: events for the notebook's open tabs.
* ``nova:nb:wake``              stream: "a runtime has work" signals.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.redis import session_store

PREFIX = "nova:nb:"
RUNTIMES = f"{PREFIX}runtimes"
WAKE = f"{PREFIX}wake"
DAY = 86_400

LIVE_STATUSES = frozenset(
    {"PROVISIONING", "STARTING", "BOOTSTRAPPING", "READY", "BUSY", "STOPPING"}
)
STARTING_STATUSES = frozenset({"PROVISIONING", "STARTING", "BOOTSTRAPPING"})


def rt_key(runtime_id: str) -> str:
    return f"{PREFIX}rt:{runtime_id}"


def channel(notebook_id: str, username: str) -> str:
    """A notebook as one person uses it: their runtime, runs and live output.

    A notebook in a shared workspace is one document but never one runtime:
    each person runs it as themselves, under their own role, and sees only
    their own results. Runtime, in-flight and event keys use this channel.
    """
    digest = hashlib.sha256(username.encode()).hexdigest()[:16]
    return f"{notebook_id}@{digest}"


def job_channel(job: dict[str, Any]) -> str:
    """A queued job's channel (jobs queued before channels: the notebook id)."""
    return str(job.get("channel") or job["notebook_id"])


def active_key(notebook_id: str) -> str:
    return f"{PREFIX}active:{notebook_id}"


def last_key(notebook_id: str) -> str:
    return f"{PREFIX}last:{notebook_id}"


def queue_key(runtime_id: str) -> str:
    return f"{PREFIX}q:{runtime_id}"


def job_key(job_id: str) -> str:
    return f"{PREFIX}job:{job_id}"


def lease_key(runtime_id: str) -> str:
    return f"{PREFIX}lease:{runtime_id}"


def exec_key(execution_id: str) -> str:
    return f"{PREFIX}exec:{execution_id}"


def inflight_key(notebook_id: str) -> str:
    return f"{PREFIX}inflight:{notebook_id}"


def cancel_key(execution_id: str) -> str:
    return f"{PREFIX}cancel:{execution_id}"


def events_key(notebook_id: str) -> str:
    return f"{PREFIX}ev:{notebook_id}"


def redis():
    client = session_store._redis
    if client is None:  # pragma: no cover - initialised by every process
        raise RuntimeError("Redis is not initialised")
    return client


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def now() -> float:
    return time.time()


@dataclass
class RuntimeState:
    id: str
    notebook_id: str
    user: str
    status: str = "PROVISIONING"
    stage: str = ""
    sandbox_id: str = ""
    context_id: str = ""
    generation: int = 1
    #: The Nova session whose identity the runtime reads data as.
    session_id: str = ""
    active_role: str = ""
    security_version: int = 0
    created_at: float = field(default_factory=now)
    ready_at: float = 0.0
    last_activity: float = field(default_factory=now)
    execution_counter: int = 0
    current_execution: str = ""
    error: str = ""
    nova_attached: bool = False
    nova_error: str = ""
    token_minted_at: float = 0.0
    token_role: str = ""
    token_version: int = 0
    #: Whose files the notebook is in: the user, or ``~ws/<id>`` for a shared
    #: workspace; and whether this person may write files back there.
    namespace: str = ""
    files_writable: bool = True

    @property
    def channel(self) -> str:
        return channel(self.notebook_id, self.user)

    @property
    def files_namespace(self) -> str:
        return self.namespace or self.user

    def to_redis(self) -> dict[str, str]:
        return {key: json.dumps(value) for key, value in asdict(self).items()}

    @classmethod
    def from_redis(cls, raw: dict[str, Any]) -> RuntimeState | None:
        if not raw:
            return None
        values: dict[str, Any] = {}
        known = cls.__dataclass_fields__
        for key, value in raw.items():
            key = key.decode() if isinstance(key, bytes) else key
            if key not in known:
                continue
            text = value.decode() if isinstance(value, bytes) else value
            try:
                values[key] = json.loads(text)
            except (TypeError, ValueError):
                values[key] = text
        if "id" not in values or "notebook_id" not in values or "user" not in values:
            return None
        return cls(**values)

    def public(self) -> dict[str, Any]:
        """What the notebook UI may see: never sandbox, context or session ids."""
        idle_for = max(0.0, now() - self.last_activity)
        status = self.status
        return {
            "runtime_id": self.id,
            "status": status,
            "stage": self.stage,
            "generation": self.generation,
            "created_at": self.created_at,
            "ready_at": self.ready_at or None,
            "last_activity": self.last_activity,
            "idle_seconds": int(idle_for),
            "active_role": self.active_role,
            "nova_attached": self.nova_attached,
            "nova_error": self.nova_error or None,
            "error": self.error or None,
            "current_execution": self.current_execution or None,
        }


async def load(runtime_id: str) -> RuntimeState | None:
    return RuntimeState.from_redis(await redis().hgetall(rt_key(runtime_id)))


async def save(state: RuntimeState, *, ttl: int) -> None:
    client = redis()
    await client.hset(rt_key(state.id), mapping=state.to_redis())
    await client.expire(rt_key(state.id), ttl)


async def update(runtime_id: str, **fields: Any) -> None:
    client = redis()
    if not await client.exists(rt_key(runtime_id)):
        return
    await client.hset(rt_key(runtime_id), mapping={k: json.dumps(v) for k, v in fields.items()})


async def active_runtime(notebook_id: str) -> RuntimeState | None:
    runtime_id = await redis().get(active_key(notebook_id))
    if not runtime_id:
        return None
    state = await load(runtime_id)
    if state is None:
        await redis().delete(active_key(notebook_id))
    return state


async def live_runtimes() -> list[RuntimeState]:
    client = redis()
    out = []
    for runtime_id in await client.smembers(RUNTIMES):
        state = await load(runtime_id)
        if state is None:
            await client.srem(RUNTIMES, runtime_id)
            continue
        out.append(state)
    return out


async def last_outcome(notebook_id: str) -> dict[str, Any] | None:
    raw = await redis().hgetall(last_key(notebook_id))
    if not raw:
        return None
    return {k: v for k, v in raw.items()}


async def put_job(runtime_id: str, job: dict[str, Any]) -> str:
    client = redis()
    job_id = job.get("job_id") or new_id("job")
    job["job_id"] = job_id
    job["runtime_id"] = runtime_id
    await client.set(job_key(job_id), json.dumps(job), ex=DAY)
    await client.rpush(queue_key(runtime_id), job_id)
    await client.expire(queue_key(runtime_id), DAY)
    await client.xadd(WAKE, {"runtime_id": runtime_id}, maxlen=1000, approximate=True)
    return job_id


async def get_job(job_id: str) -> dict[str, Any] | None:
    raw = await redis().get(job_key(job_id))
    return json.loads(raw) if raw else None


async def set_execution(execution_id: str, **fields: Any) -> None:
    client = redis()
    await client.hset(exec_key(execution_id), mapping={k: json.dumps(v) for k, v in fields.items()})
    await client.expire(exec_key(execution_id), DAY)


async def get_execution(execution_id: str) -> dict[str, Any] | None:
    raw = await redis().hgetall(exec_key(execution_id))
    if not raw:
        return None
    return {k: json.loads(v) for k, v in raw.items()}
