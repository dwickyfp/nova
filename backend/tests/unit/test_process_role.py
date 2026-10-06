"""``NOVA_PROCESS_ROLE`` decides which startup duties a process takes on.

A split deployment runs the same application as a ``web`` process and any
number of ``query`` processes. Schema bootstrap and the singleton loops must run
in exactly one of them, and neither may open the MySQL port the standalone proxy
owns; ``all`` must keep doing everything a single process did before.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main
from app.core.config import Settings, settings

CONTROL = {"bootstrap", "llm_udfs", "search_start", "newsroom", "search_stop"}
EXECUTION = {"ml_schema", "ml_sweep"}
EVERY_ROLE = {"system_pool", "capabilities", "session_store", "autopilot"}


@pytest.fixture
def duties(monkeypatch):
    """Replace every startup duty with a recorder; nothing touches a service."""
    ran: list[str] = []

    def record(name, result=None):
        async def duty(*args, **kwargs):
            ran.append(name)
            return result

        return duty

    async def forever(name):
        ran.append(name)
        await asyncio.Event().wait()

    async def until_stopped(name, stop):
        ran.append(name)
        await stop.wait()

    class Proxy:
        async def start(self):
            ran.append("proxy")

        async def stop(self):
            ran.append("proxy_stop")

    from app.modules.intelligence import newsroom_refresher
    from app.modules.ml_engine.service import ml_engine_service
    from app.modules.query_autopilot.telemetry import collector
    from app.sql_frontend.capabilities import starrocks

    monkeypatch.setattr(main, "require_configured_secrets", lambda: None)
    monkeypatch.setattr(main, "log_studio_capabilities", lambda *args: None)
    monkeypatch.setattr(main.db, "init_system_pool", record("system_pool"))
    monkeypatch.setattr(main.db, "close_system_pool", record("close_pool"))
    monkeypatch.setattr(main.db, "apply_global_time_zone", record("time_zone"))
    monkeypatch.setattr(starrocks, "resolve_engine_capabilities", record("capabilities"))
    monkeypatch.setattr(main.session_store, "init", record("session_store"))
    monkeypatch.setattr(main.session_store, "close", record("close_sessions"))
    monkeypatch.setattr(main, "_bootstrap_control_plane", record("bootstrap"))
    monkeypatch.setattr(main, "_register_llm_udfs", record("llm_udfs"))
    monkeypatch.setattr("app.proxy.server.MySQLProxyServer", Proxy)
    monkeypatch.setattr(
        ml_engine_service.ephemeral_repository, "ensure_schema", record("ml_schema")
    )
    monkeypatch.setattr(ml_engine_service, "sweep_ephemeral", lambda: forever("ml_sweep"))
    monkeypatch.setattr(main.search_service, "start", record("search_start"))
    monkeypatch.setattr(main.search_service, "stop", record("search_stop"))
    monkeypatch.setattr(newsroom_refresher, "run", lambda stop: until_stopped("newsroom", stop))
    monkeypatch.setattr(collector, "run", lambda stop, repo: until_stopped("autopilot", stop))
    return ran


async def _run_lifespan(ran: list[str]) -> set[str]:
    async with main.lifespan(main.app):
        # Let the background loops start so they are recorded.
        await asyncio.sleep(0)
    return set(ran)


@pytest.mark.parametrize(
    ("role", "expected", "absent"),
    [
        ("all", CONTROL | EXECUTION | {"proxy", "proxy_stop"}, set()),
        ("web", CONTROL, EXECUTION | {"proxy"}),
        ("query", EXECUTION, CONTROL | {"proxy"}),
    ],
)
async def test_each_role_runs_only_its_own_duties(monkeypatch, duties, role, expected, absent):
    monkeypatch.setattr(settings, "NOVA_PROCESS_ROLE", role)
    monkeypatch.setattr(settings, "PROXY_ENABLED", True)

    ran = await _run_lifespan(duties)

    assert ran >= EVERY_ROLE
    assert ran >= expected
    assert not (absent & ran)
    assert ran >= {"close_pool", "close_sessions"}


async def test_single_process_role_honours_a_disabled_proxy(monkeypatch, duties):
    monkeypatch.setattr(settings, "NOVA_PROCESS_ROLE", "all")
    monkeypatch.setattr(settings, "PROXY_ENABLED", False)

    assert "proxy" not in await _run_lifespan(duties)


@pytest.mark.parametrize(
    ("role", "enabled", "expected"),
    [("all", True, 1), ("all", False, 0), ("web", True, 0), ("query", True, 0)],
)
async def test_proxy_expected_metric_follows_the_embedded_proxy(
    monkeypatch, duties, role, enabled, expected
):
    monkeypatch.setattr(settings, "NOVA_PROCESS_ROLE", role)
    monkeypatch.setattr(settings, "PROXY_ENABLED", enabled)

    await _run_lifespan(duties)

    assert main.PROXY_EXPECTED._value.get() == expected


@pytest.mark.parametrize(
    ("role", "label"), [("all", "backend"), ("web", "backend"), ("query", "query")]
)
def test_query_tier_reports_its_own_service_label(role, label):
    assert main._service_label(role) == label


def test_unknown_role_is_rejected():
    with pytest.raises(ValidationError):
        Settings(NOVA_PROCESS_ROLE="worker")


def test_default_role_is_the_single_process_deployment():
    assert Settings.model_fields["NOVA_PROCESS_ROLE"].default == "all"


def test_health_reports_the_role(monkeypatch):
    monkeypatch.setattr(settings, "NOVA_PROCESS_ROLE", "query")
    # No context manager: the lifespan must not run for a health probe test.
    response = TestClient(main.app).get("/health")

    assert response.json()["role"] == "query"


class _Lock:
    def __init__(self, acquired: bool, events: list[str]) -> None:
        self._acquired = acquired
        self._events = events

    async def acquire(self) -> bool:
        self._events.append("acquire")
        return self._acquired

    async def release(self) -> None:
        self._events.append("release")


@pytest.fixture
def udf_registration(monkeypatch):
    events: list[str] = []
    state = {"acquired": True, "error": None}

    async def register_all_udfs():
        events.append("register")
        if state["error"]:
            raise state["error"]
        return {"registered": 7, "failed": 0}

    from app.modules.llm_functions.service import llm_function_service

    monkeypatch.setattr(llm_function_service, "register_all_udfs", register_all_udfs)
    monkeypatch.setattr(
        llm_function_service, "registration_is_current", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        "app.modules.task_orchestration.transport.LeaderLock",
        lambda *args, **kwargs: _Lock(state["acquired"], events),
    )
    return events, state


async def test_llm_functions_are_registered_under_the_lock(udf_registration):
    events, _ = udf_registration

    await main._register_llm_udfs()

    assert events == ["acquire", "register", "release"]


async def test_llm_functions_are_left_alone_while_another_process_registers(udf_registration):
    events, state = udf_registration
    state["acquired"] = False

    await main._register_llm_udfs()

    assert events == ["acquire"]


async def test_failed_registration_releases_the_lock_and_does_not_stop_startup(udf_registration):
    events, state = udf_registration
    state["error"] = RuntimeError("engine unavailable")

    await main._register_llm_udfs()

    assert events == ["acquire", "register", "release"]


@pytest.fixture
def probe(monkeypatch):
    """A started app whose engine and Redis answer unless told otherwise."""
    state = {"engine": None, "redis": None}

    async def execute_system(sql):
        if state["engine"]:
            raise state["engine"]
        return {"rows": [[1]]}

    class Redis:
        async def ping(self):
            if state["redis"]:
                raise state["redis"]
            return True

    monkeypatch.setattr(main.db, "execute_system", execute_system)
    monkeypatch.setattr(main.session_store, "_redis", Redis())
    monkeypatch.setattr(main.app.state, "started", True, raising=False)
    return state


def test_ready_once_started_and_dependencies_answer(monkeypatch, probe):
    monkeypatch.setattr(settings, "NOVA_PROCESS_ROLE", "query")

    response = TestClient(main.app).get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "role": "query"}


def test_not_ready_before_startup_has_finished(monkeypatch, probe):
    monkeypatch.setattr(main.app.state, "started", False, raising=False)

    response = TestClient(main.app).get("/ready")

    assert response.status_code == 503 and response.json() == {"status": "starting"}


@pytest.mark.parametrize("dependency", ["engine", "redis"])
def test_not_ready_while_a_dependency_is_down(probe, dependency):
    probe[dependency] = ConnectionError("secret-host:9030 refused")

    response = TestClient(main.app).get("/ready")

    assert response.status_code == 503
    # The cause is logged, not sent to an unauthenticated caller.
    assert response.json() == {"status": "unavailable"}


def test_health_stays_up_while_a_dependency_is_down(probe):
    probe["engine"] = ConnectionError("down")

    assert TestClient(main.app).get("/health").status_code == 200


async def test_lifespan_marks_the_process_started_only_while_it_serves(monkeypatch, duties):
    monkeypatch.setattr(settings, "NOVA_PROCESS_ROLE", "query")
    monkeypatch.setattr(main.app.state, "started", False, raising=False)

    async with main.lifespan(main.app):
        assert main.app.state.started is True

    assert main.app.state.started is False


# ── Schema bootstrap across web replicas ────────────────────────────────────


class _SharedLock:
    """Two ``LeaderLock`` handles over one in-memory key."""

    held = False

    def __init__(self, *args, **kwargs) -> None:
        self.mine = False

    async def acquire(self) -> bool:
        if type(self).held:
            return False
        type(self).held = self.mine = True
        return True

    async def release(self) -> None:
        if self.mine:
            type(self).held = self.mine = False


@pytest.fixture
def bootstrap_lock(monkeypatch):
    _SharedLock.held = False
    monkeypatch.setattr("app.modules.task_orchestration.transport.LeaderLock", _SharedLock)
    monkeypatch.setattr(main, "BOOTSTRAP_LOCK_POLL_SECONDS", 0.001)
    return _SharedLock


async def test_two_starting_processes_never_bootstrap_at_the_same_time(monkeypatch, bootstrap_lock):
    running = peak = finished = 0

    async def bootstrap():
        nonlocal running, peak, finished
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        finished += 1

    monkeypatch.setattr(main, "_bootstrap_control_plane", bootstrap)

    await asyncio.gather(main._bootstrap_exclusively(), main._bootstrap_exclusively())

    # Both ran it (the second finds everything in place), one after the other.
    assert (peak, finished) == (1, 2) and bootstrap_lock.held is False


async def test_bootstrap_lock_is_released_when_bootstrap_fails(monkeypatch, bootstrap_lock):
    async def bootstrap():
        raise RuntimeError("engine unavailable")

    monkeypatch.setattr(main, "_bootstrap_control_plane", bootstrap)

    with pytest.raises(RuntimeError):
        await main._bootstrap_exclusively()

    assert bootstrap_lock.held is False


async def test_bootstrap_proceeds_when_the_lock_is_never_released(monkeypatch, bootstrap_lock):
    ran = []
    bootstrap_lock.held = True
    monkeypatch.setattr(main, "BOOTSTRAP_LOCK_TTL_SECONDS", 0.01)

    async def bootstrap():
        ran.append(True)

    monkeypatch.setattr(main, "_bootstrap_control_plane", bootstrap)

    await main._bootstrap_exclusively()

    # A crashed holder must not keep every other process from starting, and
    # the waiter must not release a lock it never held.
    assert ran == [True] and bootstrap_lock.held is True


async def test_bootstrap_runs_without_a_lock_when_redis_is_unavailable(monkeypatch):
    ran = []

    class Broken:
        def __init__(self, *args, **kwargs):
            pass

        async def acquire(self):
            raise ConnectionError("redis down")

    async def bootstrap():
        ran.append(True)

    monkeypatch.setattr("app.modules.task_orchestration.transport.LeaderLock", Broken)
    monkeypatch.setattr(main, "_bootstrap_control_plane", bootstrap)

    await main._bootstrap_exclusively()

    assert ran == [True]


async def test_current_llm_functions_are_not_dropped_and_recreated(monkeypatch, udf_registration):
    from app.modules.llm_functions.service import llm_function_service

    events, _ = udf_registration
    monkeypatch.setattr(
        llm_function_service, "registration_is_current", AsyncMock(return_value=True)
    )

    await main._register_llm_udfs()

    assert events == ["acquire", "release"]
