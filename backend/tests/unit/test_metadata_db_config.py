import pytest

from app.core.metadata_db import MetadataDatabase, MetadataPoolConfig, MetadataUnavailable
from app.modules.access_control.security_context import SecurityContext

DSN = "host=localhost dbname=nova_control user=app password=canary sslmode=verify-full"


@pytest.mark.parametrize(
    "change",
    [
        {"max_size": 0},
        {"max_waiting": 0},
        {"acquire_timeout": 0},
        {"acquire_timeout": float("nan")},
        {"acquire_timeout": float("inf")},
        {"statement_timeout_ms": 0},
        {"lock_timeout_ms": 0},
        {"idle_transaction_timeout_ms": 0},
    ],
)
def test_pool_and_transaction_limits_cannot_be_disabled(change):
    with pytest.raises(ValueError, match="budgets must be positive"):
        MetadataPoolConfig(DSN, **change)


@pytest.mark.parametrize(
    "dsn",
    [
        "host=localhost dbname=nova_control sslmode=disable",
        "host=localhost dbname=nova_control sslmode=require",
        "host=localhost dbname=superset sslmode=verify-full",
        "synthetic-invalid-canary",
        "dbname=nova_control sslmode=verify-full",
        "host=/tmp dbname=nova_control sslmode=verify-full",
        "host=@local-socket dbname=nova_control sslmode=verify-full",
        "host=localhost, dbname=nova_control sslmode=verify-full",
    ],
)
def test_insecure_or_wrong_database_configuration_fails_without_secret_readback(dsn):
    with pytest.raises(ValueError) as caught:
        MetadataPoolConfig(dsn)
    assert dsn not in str(caught.value)


def test_config_repr_does_not_include_connection_credentials():
    assert "canary" not in repr(MetadataPoolConfig(DSN))
    assert "password" not in repr(MetadataPoolConfig(DSN))


@pytest.mark.asyncio
async def test_unopened_database_and_missing_actor_fail_before_connection():
    db = MetadataDatabase(MetadataPoolConfig(DSN))
    try:
        with pytest.raises(ValueError, match="actor"):
            async with db.transaction(None):
                pytest.fail("Missing actor was admitted")
        with pytest.raises(MetadataUnavailable):
            async with db.transaction(SecurityContext(principal="alice", active_role="analyst")):
                pytest.fail("Uninitialized schema was admitted")
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id", ["", " ", "x" * 65, "synthetic\0login", 3, []])
async def test_invalid_login_context_fails_before_connection_without_readback(session_id):
    db = MetadataDatabase(MetadataPoolConfig(DSN))
    try:
        with pytest.raises(ValueError, match="^Invalid metadata login context$"):
            async with db.transaction(SecurityContext("alice", "analyst", session_id=session_id)):
                pytest.fail("An invalid login reached a metadata transaction")
    finally:
        await db.close()
