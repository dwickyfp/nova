"""A wrong password and an unreachable engine are different answers at sign-in."""

from __future__ import annotations

import asyncmy.errors
import pytest

from app.core.exceptions import StarRocksError
from app.modules.auth import service as auth_service
from app.modules.auth.exceptions import SignInUnavailableError


def connect_failing(error):
    async def connect(**_):
        raise error

    return connect


async def test_a_wrong_password_is_invalid_credentials(monkeypatch):
    monkeypatch.setattr(auth_service.asyncmy, "connect", connect_failing(
        asyncmy.errors.OperationalError(1045, "Access denied")))
    assert await auth_service.AuthService().verify_credentials("alice", "wrong") is False


@pytest.mark.parametrize("error", [
    asyncmy.errors.OperationalError(2003, "Can't connect to MySQL server"),
    asyncmy.errors.OperationalError(1203, "too many connections"),
    ConnectionRefusedError(61, "Connection refused"),
    TimeoutError(),
])
async def test_an_unreachable_engine_is_not_a_wrong_password(monkeypatch, error):
    monkeypatch.setattr(auth_service.asyncmy, "connect", connect_failing(error))
    with pytest.raises(SignInUnavailableError) as raised:
        await auth_service.AuthService().verify_credentials("alice", "right")
    assert raised.value.status_code == 503
    assert "password" not in raised.value.message.lower()


@pytest.mark.parametrize(("error", "unavailable"), [
    (StarRocksError("Connection error: lost"), True),
    (StarRocksError("SQL error: (1203) x", engine_code=1203), True),
    (StarRocksError("SQL error: (1040) x", engine_code=1040), True),
    (StarRocksError("SQL error: (1142) denied", engine_code=1142), False),
    (StarRocksError("SQL error: (1064) syntax", engine_code=1064), False),
    (StarRocksError("SQL error: something"), False),
])
def test_engine_unavailable_is_connection_or_capacity_only(error, unavailable):
    assert error.engine_unavailable is unavailable


@pytest.mark.parametrize(("detail", "overloaded"), [
    # Observed live under load (dashboard-perf-live-attempt-8): a planner timeout
    # in a dataset's source check had been answered as "not permitted".
    ("StarRocks planner use long time 3324 ms in logical phase, This probably because "
     "1. FE Full GC", True),
    ("Query exceeded time limit of 300 seconds", True),
    ("Memory of process exceed limit. QUERY Backend: 172.17.0.3", True),
    ("Backend node not found. Check if any backend node is down.", True),
    ("Getting analyzing error. Detail message: Column 'x' cannot be resolved.", False),
    ("Unknown database 'private_db'", False),
])
def test_engine_overload_is_unavailable_not_a_refusal(detail, overloaded):
    from app.modules.query.repository import _starrocks_error

    error = _starrocks_error(asyncmy.errors.ProgrammingError(1064, detail))
    assert (error.overloaded, error.engine_unavailable) == (overloaded, overloaded)
    # The private wording that replaces the engine message keeps the classification.
    private = StarRocksError("Dataset query execution failed", engine_code=error.engine_code,
                             overloaded=error.overloaded)
    assert private.engine_unavailable is overloaded


def test_only_the_generic_engine_code_is_read_as_overload():
    from app.modules.query.repository import _starrocks_error

    error = _starrocks_error(asyncmy.errors.ProgrammingError(
        1142, "SELECT command denied: query timeout"))
    assert not error.engine_unavailable
