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
async def test_single_role_owns_only_its_lifecycle(role, monkeypatch):
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
    session.close.assert_awaited_once()
    pool.close_system_pool.assert_awaited_once()


@pytest.mark.parametrize("failure", ["pool", "session", "ping", "build", "metrics"])
async def test_partial_startup_releases_opened_dependencies(failure, monkeypatch):
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
