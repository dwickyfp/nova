"""A dataset read runs every statement on one engine session as the caller."""

from __future__ import annotations

from contextlib import asynccontextmanager

import asyncmy.errors
import pytest

from app.core.exceptions import SessionExpiredError, StarRocksError
from app.core.security import encrypt_password
from app.modules.query.service import delegated_connection, delegated_connection_for
from app.modules.viz import dataset_service

pytestmark = pytest.mark.engine_session


def user(name="alice"):
    return {"username": name, "encrypted_password": encrypt_password("secret")}


@pytest.fixture
def opened(monkeypatch):
    calls: list[dict] = []
    state = {"open": 0, "fail": None}

    @asynccontextmanager
    async def user_conn(*, username, password, database=None):
        calls.append({"username": username, "password": password})
        if state["fail"] is not None:
            raise state["fail"]
        connection = object()
        state["open"] += 1
        try:
            yield connection
        finally:
            state["open"] -= 1

    from app.core.database import db

    monkeypatch.setattr(db, "user_conn", user_conn)
    return calls, state


async def test_one_session_as_the_caller_serves_the_whole_request(opened):
    calls, state = opened
    async with dataset_service.held_engine_session(user()):
        first = delegated_connection_for("alice")
        assert first is not None and delegated_connection_for("alice") is first
        assert delegated_connection_for("bob") is None  # never another account's
        assert state["open"] == 1
    assert calls == [{"username": "alice", "password": "secret"}]
    assert state["open"] == 0 and delegated_connection_for("alice") is None


async def test_the_session_closes_when_the_request_fails(opened):
    _, state = opened
    with pytest.raises(RuntimeError):
        async with dataset_service.held_engine_session(user()):
            raise RuntimeError("timed out")
    assert state["open"] == 0 and delegated_connection_for("alice") is None


async def test_a_scheduled_runs_worker_session_is_kept(opened):
    calls, _ = opened
    worker = object()
    with delegated_connection("alice", worker):
        async with dataset_service.held_engine_session(user()):
            assert delegated_connection_for("alice") is worker
    assert calls == []


async def test_a_changed_password_ends_the_session(opened):
    _, state = opened
    state["fail"] = asyncmy.errors.OperationalError(1045, "Access denied")
    with pytest.raises(SessionExpiredError):
        async with dataset_service.held_engine_session(user()):
            pass


@pytest.mark.parametrize("failure", [
    asyncmy.errors.OperationalError(2003, "Can't connect"),
    asyncmy.errors.OperationalError(1203, "too many connections"),
    ConnectionRefusedError(61, "refused"),
])
async def test_an_unreachable_engine_is_unavailable(opened, failure):
    _, state = opened
    state["fail"] = failure
    with pytest.raises(StarRocksError) as raised:
        async with dataset_service.held_engine_session(user()):
            pass
    assert raised.value.engine_unavailable
    assert "secret" not in raised.value.message
