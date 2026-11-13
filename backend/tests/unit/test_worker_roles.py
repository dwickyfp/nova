"""Separate lifetimes, admission prerequisites and failure propagation per workload."""

from __future__ import annotations

import asyncio
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.config import settings
from app.worker import __main__ as entry
from app.worker import supervisor
from app.worker.health import healthy


@pytest.fixture(autouse=True)
def metadata(monkeypatch):
    """Every role first admits itself to nova_control; stand that in here."""
    state = SimpleNamespace(
        database=object(),
        open=AsyncMock(),
        close=AsyncMock(),
        settings=SimpleNamespace(readiness_interval_seconds=1.0),
    )
    state.open.return_value = state.database
    monkeypatch.setattr(entry, "load_runtime_settings", Mock(return_value=state.settings))
    monkeypatch.setattr(entry, "open_process_metadata", state.open)
    monkeypatch.setattr(entry, "close_process_metadata", state.close)
    monkeypatch.setattr(entry, "pool_config", Mock())
    return state


@pytest.mark.parametrize("role", ["task", "automation"])
def test_only_delegating_roles_require_impersonation(role, monkeypatch):
    monkeypatch.setattr(settings, "WORKER_IMPERSONATION_USER", "")
    monkeypatch.setattr(settings, "WORKER_IMPERSONATION_PASSWORD", "")
    with pytest.raises(RuntimeError, match="credentials"):
        entry.validate_role(role)
    for other in {"ml", "ingestion", "notebook", "autopilot"}:
        entry.validate_role(other)


@pytest.mark.parametrize("role", ["task", "automation"])
@pytest.mark.parametrize("username", ["root", "ROOT", "nova_admin"])
def test_delegation_rejects_privileged_account(role, username, monkeypatch):
    monkeypatch.setattr(settings, "WORKER_IMPERSONATION_USER", username)
    monkeypatch.setattr(settings, "WORKER_IMPERSONATION_PASSWORD", "test-only")
    with pytest.raises(RuntimeError, match="unprivileged"):
        entry.validate_role(role)


async def test_unknown_role_does_not_open_dependencies(monkeypatch):
    monkeypatch.setattr(entry, "require_configured_secrets", Mock())
    pool = AsyncMock()
    monkeypatch.setattr(entry.db, "init_system_pool", pool)
    with pytest.raises(ValueError, match="Unknown worker role"):
        await entry._run("typo")
    pool.assert_not_awaited()


@pytest.mark.parametrize("failed", ["workload", "heartbeat"])
@pytest.mark.parametrize("raises", [False, True])
async def test_unexpected_exit_stops_sibling_and_fails(failed, raises):
    stopped = asyncio.Event()
    sibling_stopped = asyncio.Event()

    async def crash(stop):
        if raises:
            raise ValueError("fixture failure")

    async def sibling(stop):
        await stop.wait()
        sibling_stopped.set()

    work = SimpleNamespace(run_forever=crash if failed == "workload" else sibling)
    health = SimpleNamespace(run=crash if failed == "heartbeat" else sibling)
    with pytest.raises(ValueError if raises else RuntimeError):
        await entry.run_workload(work, health, stopped, shutdown_seconds=0.1)
    assert stopped.is_set()
    assert sibling_stopped.is_set()


async def test_graceful_stop_waits_for_work_cleanup():
    stop = asyncio.Event()
    cleaned = []

    async def run(event):
        await event.wait()
        await asyncio.sleep(0)
        cleaned.append(True)

    task = asyncio.create_task(entry.run_workload(
        SimpleNamespace(run_forever=run), SimpleNamespace(run=run), stop
    ))
    await asyncio.sleep(0)
    stop.set()
    await asyncio.wait_for(task, 1)
    assert len(cleaned) == 2


async def test_drain_timeout_cancels_work():
    stop = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked(event):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(entry.run_workload(
        SimpleNamespace(run_forever=blocked),
        SimpleNamespace(run=lambda event: event.wait()), stop,
        shutdown_seconds=0.01,
    ))
    await asyncio.sleep(0)
    stop.set()
    await asyncio.wait_for(task, 1)
    assert cancelled.is_set()


@pytest.mark.parametrize("role", supervisor.ROLES)
async def test_single_role_owns_only_its_lifecycle(role, monkeypatch, metadata):
    monkeypatch.setattr(entry, "require_configured_secrets", Mock())
    monkeypatch.setattr(entry, "validate_role", Mock())
    pool = SimpleNamespace(init_system_pool=AsyncMock(), close_system_pool=AsyncMock())
    session = SimpleNamespace(init=AsyncMock(), close=AsyncMock())
    client = SimpleNamespace(ping=AsyncMock(), aclose=AsyncMock())
    health = SimpleNamespace(publish=AsyncMock(), close=AsyncMock())
    factory = Mock(return_value=health)
    build = AsyncMock(return_value=object())
    run = AsyncMock()
    monkeypatch.setattr(entry, "db", pool)
    monkeypatch.setattr(entry, "session_store", session)
    monkeypatch.setattr(entry.aioredis, "from_url", Mock(return_value=client))
    monkeypatch.setattr(entry, "WorkerProcessHeartbeat", factory)
    monkeypatch.setattr(entry, "build_workload", build)
    monkeypatch.setattr(entry, "run_workload", run)
    monkeypatch.setattr(entry, "start_metrics_server", Mock())
    await entry._run(role)
    build.assert_awaited_once_with(role, client)
    assert factory.call_args.kwargs["registry_key"] == (
        settings.WORKER_REGISTRY_KEY if role == "task"
        else f"{settings.WORKER_REGISTRY_KEY}:{role}"
    )
    assert factory.call_args.kwargs["service"] == entry.METRICS_SERVICES[role]
    assert run.call_args.args[0] is build.return_value
    health.close.assert_awaited_once()
    client.aclose.assert_awaited_once()
    if role == "outbox":
        session.init.assert_not_awaited()
        session.close.assert_not_awaited()
        pool.init_system_pool.assert_not_awaited()
        pool.close_system_pool.assert_not_awaited()
        entry.require_configured_secrets.assert_not_called()
    else:
        session.close.assert_awaited_once()
        pool.close_system_pool.assert_awaited_once()
    metadata.open.assert_awaited_once()
    assert metadata.open.call_args.args == (role,)
    metadata.close.assert_awaited_once_with(metadata.database, entry.METRICS_SERVICES[role])
    assert len(run.call_args.kwargs["monitors"]) == 1


@pytest.mark.parametrize("failure", ["pool", "session", "ping", "build", "metrics"])
async def test_partial_startup_releases_opened_dependencies(failure, monkeypatch, metadata):
    monkeypatch.setattr(entry, "require_configured_secrets", Mock())
    pool = SimpleNamespace(init_system_pool=AsyncMock(), close_system_pool=AsyncMock())
    session = SimpleNamespace(init=AsyncMock(), close=AsyncMock())
    client = SimpleNamespace(ping=AsyncMock(), aclose=AsyncMock())
    health = SimpleNamespace(publish=AsyncMock(), close=AsyncMock())
    build, metrics = AsyncMock(), Mock()
    {"pool": pool.init_system_pool, "session": session.init, "ping": client.ping,
     "build": build, "metrics": metrics}[failure].side_effect = ValueError("fixture")
    monkeypatch.setattr(entry, "db", pool)
    monkeypatch.setattr(entry, "session_store", session)
    monkeypatch.setattr(entry.aioredis, "from_url", Mock(return_value=client))
    monkeypatch.setattr(entry, "WorkerProcessHeartbeat", Mock(return_value=health))
    monkeypatch.setattr(entry, "build_workload", build)
    monkeypatch.setattr(entry, "start_metrics_server", metrics)
    with pytest.raises(ValueError, match="fixture"):
        await entry._run("ml")
    pool.close_system_pool.assert_awaited_once()
    if failure != "pool":
        session.close.assert_awaited_once()
        client.aclose.assert_awaited_once()
    if failure == "metrics":
        health.close.assert_awaited_once()
    metadata.close.assert_awaited_once_with(metadata.database, "ml-worker")


class Child:
    def __init__(self, *, ignore_term=False):
        self.returncode = None
        self.exited = asyncio.Event()
        self.terminated = False
        self.killed = False
        self.ignore_term = ignore_term

    async def wait(self):
        await self.exited.wait()
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self.ignore_term:
            self.exit(-15)

    def kill(self):
        self.killed = True
        self.exit(-9)

    def exit(self, status):
        self.returncode = status
        self.exited.set()


@pytest.mark.parametrize("reason", ["stop", "crash", "spawn", "timeout"])
async def test_supervisor_reaps_children_on_every_exit(reason, monkeypatch):
    stop = asyncio.Event()
    children, commands = [], []

    async def spawn(*command, **kwargs):
        if reason == "spawn" and len(children) == 2:
            raise OSError("fixture spawn failure")
        child = Child(ignore_term=reason == "timeout")
        child.pid = len(children) + 1000
        children.append(child)
        commands.append(command)
        assert kwargs["start_new_session"] is True
        assert kwargs["env"][supervisor.PARENT_PID_ENV] == str(supervisor.os.getpid())
        if len(children) == len(supervisor.ROLES):
            if reason == "crash":
                children[2].exit(1)
            else:
                stop.set()
        return child

    monkeypatch.setattr(supervisor.asyncio, "create_subprocess_exec", spawn)

    def kill_group(pid, sig):
        assert sig == signal.SIGKILL
        child = next(child for child in children if child.pid == pid)
        if child.returncode is None:
            child.kill()

    group_kill = Mock(side_effect=kill_group)
    monkeypatch.setattr(supervisor.os, "killpg", group_kill)
    if reason in {"crash", "spawn"}:
        with pytest.raises(RuntimeError if reason == "crash" else OSError):
            await supervisor.supervise_processes(stop, shutdown_seconds=0.01)
    else:
        await supervisor.supervise_processes(stop, shutdown_seconds=0.01)
    assert len(children) == (2 if reason == "spawn" else len(supervisor.ROLES))
    assert [command[-1] for command in commands] == list(supervisor.ROLES[:len(children)])
    assert all(child.returncode is not None for child in children)
    assert all(child.terminated for child in children if child.returncode != 1)
    assert group_kill.call_count == len(children)
    if reason == "timeout":
        assert all(child.killed for child in children)


@pytest.mark.parametrize("stamp,up,service,expected", [
    (100, 1, "ml-worker", True),
    (50, 1, "ml-worker", False),
    (101, 1, "ml-worker", False),
    (100, 0, "ml-worker", False),
    (100, 1, "worker", False),
    (0, 1, "ml-worker", False),
])
def test_health_requires_this_roles_fresh_heartbeat(stamp, up, service, expected):
    body = (
        f'nova_service_up{{service="{service}"}} {up}\n'
        f'nova_service_last_heartbeat_timestamp_seconds{{service="{service}"}} {stamp}\n'
    )
    assert healthy(body, "ml-worker", now=100, stale_seconds=30) is expected


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _planner_timeout():
    import asyncmy.errors

    return asyncmy.errors.ProgrammingError(1064, "planner use long time")


async def test_readiness_retries_transient_engine_failure(monkeypatch):
    clock, stop, calls = FakeClock(), asyncio.Event(), []

    async def instant_wait(awaitable, timeout):
        awaitable.close()
        clock.now += timeout
        raise TimeoutError

    monkeypatch.setattr(entry.asyncio, "wait_for", instant_wait)

    async def prepare():
        calls.append(clock.now)
        if len(calls) < 3:
            raise _planner_timeout()
        return "ready"

    assert await entry.await_dependencies(prepare, stop, role="task", clock=clock) == "ready"
    assert calls == [0.0, 1.0, 3.0]


async def test_readiness_gives_up_after_deadline(monkeypatch):
    clock, stop = FakeClock(), asyncio.Event()

    async def instant_wait(awaitable, timeout):
        awaitable.close()
        clock.now += timeout
        raise TimeoutError

    monkeypatch.setattr(entry.asyncio, "wait_for", instant_wait)
    prepare = AsyncMock(side_effect=ConnectionRefusedError("engine down"))
    with pytest.raises(ConnectionRefusedError):
        await entry.await_dependencies(prepare, stop, role="task", clock=clock,
                                       deadline_seconds=20)
    assert clock.now <= 20
    assert prepare.await_count == 5  # 0, 1, 3, 7, 15 seconds


async def test_readiness_does_not_retry_configuration_errors():
    prepare = AsyncMock(side_effect=ValueError("bad configuration"))
    with pytest.raises(ValueError):
        await entry.await_dependencies(prepare, asyncio.Event(), role="ml")
    prepare.assert_awaited_once()


async def test_shutdown_while_waiting_returns_without_workload():
    stop = asyncio.Event()

    async def prepare():
        stop.set()
        raise _planner_timeout()

    assert await entry.await_dependencies(prepare, stop, role="notebook") is None


async def test_role_waits_for_engine_instead_of_exiting(monkeypatch):
    monkeypatch.setattr(entry, "require_configured_secrets", Mock())
    monkeypatch.setattr(entry, "validate_role", Mock())
    monkeypatch.setattr(entry, "db", SimpleNamespace(
        init_system_pool=AsyncMock(), close_system_pool=AsyncMock()))
    session = SimpleNamespace(init=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(entry, "session_store", session)
    client = SimpleNamespace(ping=AsyncMock(), aclose=AsyncMock())
    monkeypatch.setattr(entry.aioredis, "from_url", Mock(return_value=client))
    health = SimpleNamespace(publish=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(entry, "WorkerProcessHeartbeat", Mock(return_value=health))
    monkeypatch.setattr(entry, "start_metrics_server", Mock())
    workload = object()
    build = AsyncMock(side_effect=[_planner_timeout(), workload])
    monkeypatch.setattr(entry, "build_workload", build)
    run = AsyncMock()
    monkeypatch.setattr(entry, "run_workload", run)

    async def instant_wait(awaitable, timeout):
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(entry.asyncio, "wait_for", instant_wait)
    await entry._run("task")
    assert build.await_count == 2
    assert run.await_count == 1 and run.call_args.args[0] is workload
    health.publish.assert_awaited_once()


async def test_role_stops_when_its_launcher_dies(monkeypatch):
    stop = asyncio.Event()
    parents = iter([100, 100, 1])
    monkeypatch.setattr(entry.os, "getppid", lambda: next(parents))
    await asyncio.wait_for(entry.watch_parent(stop, 100, interval=0.01), 1)
    assert stop.is_set()


async def test_parent_watch_ends_with_normal_shutdown():
    stop = asyncio.Event()
    task = asyncio.create_task(entry.watch_parent(stop, entry.os.getppid(), interval=0.01))
    await asyncio.sleep(0.03)
    assert not task.done()
    stop.set()
    await asyncio.wait_for(task, 1)


async def test_metrics_port_held_by_draining_role_is_retried(monkeypatch):
    monkeypatch.setattr(entry, "require_configured_secrets", Mock())
    monkeypatch.setattr(entry, "validate_role", Mock())
    monkeypatch.delenv(supervisor.PARENT_PID_ENV, raising=False)
    monkeypatch.setattr(entry, "db", SimpleNamespace(
        init_system_pool=AsyncMock(), close_system_pool=AsyncMock()))
    session = SimpleNamespace(init=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(entry, "session_store", session)
    client = SimpleNamespace(ping=AsyncMock(), aclose=AsyncMock())
    monkeypatch.setattr(entry.aioredis, "from_url", Mock(return_value=client))
    monkeypatch.setattr(entry, "WorkerProcessHeartbeat", Mock(return_value=SimpleNamespace(
        publish=AsyncMock(), close=AsyncMock())))
    monkeypatch.setattr(entry, "build_workload", AsyncMock(return_value=object()))
    metrics = Mock(side_effect=[OSError(48, "Address already in use"), None])
    monkeypatch.setattr(entry, "start_metrics_server", metrics)
    run = AsyncMock()
    monkeypatch.setattr(entry, "run_workload", run)

    async def instant_wait(awaitable, timeout):
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(entry.asyncio, "wait_for", instant_wait)
    await entry._run("ml")
    assert metrics.call_count == 2
    run.assert_awaited_once()


@pytest.mark.parametrize("error", ["mismatch", "configuration"])
async def test_metadata_refusal_stops_the_role_before_any_other_dependency(
    error, monkeypatch, metadata,
):
    from app.core.metadata_db import MetadataSchemaMismatch
    from app.core.metadata_process import MetadataConfigurationError

    monkeypatch.setattr(entry, "require_configured_secrets", Mock())
    pool = SimpleNamespace(init_system_pool=AsyncMock(), close_system_pool=AsyncMock())
    monkeypatch.setattr(entry, "db", pool)
    from_url = Mock()
    monkeypatch.setattr(entry.aioredis, "from_url", from_url)
    failure = MetadataSchemaMismatch() if error == "mismatch" else MetadataConfigurationError()
    metadata.open.side_effect = failure
    with pytest.raises(type(failure)):
        await entry._run("ml")
    pool.init_system_pool.assert_not_awaited()
    from_url.assert_not_called()
    metadata.close.assert_not_awaited()


async def test_shutdown_while_waiting_for_metadata_opens_nothing_else(monkeypatch, metadata):
    monkeypatch.setattr(entry, "require_configured_secrets", Mock())
    pool = SimpleNamespace(init_system_pool=AsyncMock(), close_system_pool=AsyncMock())
    monkeypatch.setattr(entry, "db", pool)
    metadata.open.return_value = None
    await entry._run("maintenance")
    assert metadata.open.call_args.kwargs["stop"] is not None
    pool.init_system_pool.assert_not_awaited()
    metadata.close.assert_not_awaited()


async def test_launcher_refuses_when_any_role_lacks_its_metadata_login(monkeypatch):
    from app.core.metadata_process import MetadataConfigurationError

    monkeypatch.setattr(entry, "require_configured_secrets", Mock())
    monkeypatch.setattr(entry, "validate_role", Mock())
    monkeypatch.setattr(entry, "pool_config", Mock(side_effect=MetadataConfigurationError()))
    supervise = AsyncMock()
    monkeypatch.setattr(entry, "supervise_processes", supervise)
    with pytest.raises(MetadataConfigurationError):
        await entry._run_all()
    supervise.assert_not_awaited()


async def test_monitors_run_beside_the_workload_and_stop_with_it():
    stop = asyncio.Event()
    observed = []

    class Work:
        async def run_forever(self, stopped):
            await asyncio.sleep(0)
            stopped.set()

    heartbeat = SimpleNamespace(run=lambda stopped: stopped.wait())

    async def monitor(stopped):
        observed.append("started")
        await stopped.wait()
        observed.append("stopped")

    await entry.run_workload(Work(), heartbeat, stop, monitors=(monitor,))
    assert observed == ["started", "stopped"]


@pytest.mark.parametrize("ready_value,expected", [(1, True), (0, False), (None, False)])
def test_readiness_also_requires_metadata(ready_value, expected):
    body = (
        'nova_service_up{service="ml-worker"} 1\n'
        'nova_service_last_heartbeat_timestamp_seconds{service="ml-worker"} 100\n'
    )
    if ready_value is not None:
        body += f'nova_metadata_ready{{service="ml-worker"}} {ready_value}\n'
    assert healthy(body, "ml-worker", now=100, stale_seconds=30) is True
    assert healthy(body, "ml-worker", now=100, stale_seconds=30, ready=True) is expected
