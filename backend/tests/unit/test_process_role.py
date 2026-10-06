"""``NOVA_PROCESS_ROLE`` decides which startup duties a process takes on.

A split deployment runs the same application as a ``web`` process and any
number of ``query`` processes. Schema bootstrap and the singleton loops must run
in exactly one of them, and neither may open the MySQL port the standalone proxy
owns; ``all`` must keep doing everything a single process did before.
"""

from __future__ import annotations

import asyncio

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
