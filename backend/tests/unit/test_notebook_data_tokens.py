"""Notebook data tokens: the runtime's governed, read-only proxy password.

A notebook runtime's DuckDB logs in to the MySQL proxy with a token minted for
the signed-in user's Nova session and the runtime. These tests pin that the
token lives in its own namespace, dies with the runtime and with the session's
role, is accepted only from sandbox networks, and that every statement on such
a connection is read-only.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.redis import session_store
from app.core.security import encrypt_password
from app.modules.access_control import data_tokens
from app.modules.superset import access
from app.proxy.executor import ProxyQueryExecutor, _read_only_statement
from app.proxy.protocol import scramble_native_password
from app.proxy.session import SessionState
from tests.unit.test_superset_bridge import FakeRedis


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(session_store, "_redis", fake)

    async def exists(key):
        return int(fake._live(key) is not None)

    fake.exists = exists
    return fake


async def _session(role: str = "analyst") -> data_tokens.BiSession:
    session_id = await session_store.create(
        "alice", encrypt_password("pw"), ["analyst", "finance"],
        default_role="analyst", active_role=role,
    )
    return await data_tokens.resolve_session(session_id)


async def _mint(session, runtime_id="rt-1") -> data_tokens.DataToken:
    return await data_tokens.mint(
        session, purpose=data_tokens.NOTEBOOK, ttl=1800, max_per_principal=4,
        subject=runtime_id,
    )


async def _runtime_live(redis, runtime_id="rt-1"):
    await redis.hset(f"nova:nb:rt:{runtime_id}", mapping={"status": "READY"})


async def test_notebook_tokens_live_in_their_own_namespace(redis):
    session = await _session()
    issued = await _mint(session)
    assert redis.keys_matching("nova:nb:token:*") and not redis.keys_matching("nova:bi:token:*")
    scramble = b"x" * 20
    response = scramble_native_password(issued.token, scramble)
    # Not a BI token…
    assert await data_tokens.match("alice", scramble, response, purpose=data_tokens.BI) is None
    # …but a notebook one, bound to the runtime and read-only.
    binding = await data_tokens.match("alice", scramble, response, purpose=data_tokens.NOTEBOOK)
    assert binding is not None and binding.subject == "rt-1" and binding.read_only


async def test_the_token_is_reused_per_runtime_and_not_across_runtimes(redis):
    session = await _session()
    first = await _mint(session, "rt-1")
    assert (await _mint(session, "rt-1")).token == first.token
    assert (await _mint(session, "rt-2")).token != first.token


async def test_binding_holds_only_while_the_runtime_is_live(redis):
    session = await _session()
    issued = await _mint(session)
    scramble = b"y" * 20
    binding = await data_tokens.match(
        "alice", scramble, scramble_native_password(issued.token, scramble),
        purpose=data_tokens.NOTEBOOK,
    )
    with pytest.raises(data_tokens.BiCredentialError, match="stopped"):
        await data_tokens.verify_binding(binding)
    await _runtime_live(redis)
    assert (await data_tokens.verify_binding(binding)).username == "alice"


async def test_a_role_switch_breaks_the_binding(redis):
    session = await _session()
    await _runtime_live(redis)
    issued = await _mint(session)
    scramble = b"z" * 20
    binding = await data_tokens.match(
        "alice", scramble, scramble_native_password(issued.token, scramble),
        purpose=data_tokens.NOTEBOOK,
    )
    await session_store.set_active_role(session.session_id, "finance")
    with pytest.raises(data_tokens.BiCredentialError, match="changed"):
        await data_tokens.verify_binding(binding)


async def test_revoking_the_runtime_forgets_its_token(redis):
    session = await _session()
    issued = await _mint(session)
    await data_tokens.revoke_subject(data_tokens.NOTEBOOK, "rt-1")
    scramble = b"w" * 20
    response = scramble_native_password(issued.token, scramble)
    assert await data_tokens.match(
        "alice", scramble, response, purpose=data_tokens.NOTEBOOK
    ) is None


def test_notebook_tokens_are_accepted_only_from_sandbox_networks(monkeypatch):
    monkeypatch.setattr(settings, "NOTEBOOK_SANDBOX_NETWORKS", "0.250.250.0/24")
    assert access.notebook_peer_allowed("0.250.250.254")
    assert not access.notebook_peer_allowed("10.0.0.5")
    assert not access.notebook_peer_allowed("not-an-ip")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM t",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "SHOW TABLES",
        "SET autocommit=1",
        "SET NAMES utf8mb4",
        "SET @@session.time_zone = '+00:00'",
        "SET character_set_results = NULL",
    ],
)
def test_reads_and_connection_settings_are_allowed(sql):
    assert _read_only_statement(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "DELETE FROM t",
        "DROP TABLE t",
        "SET PASSWORD = 'x'",
        "SET GLOBAL max_connections = 1",
        "SET autocommit=1; DROP TABLE t",
        "SELECT * FROM t INTO OUTFILE '/tmp/x'",
        "GRANT SELECT ON t TO u",
    ],
)
def test_writes_and_other_settings_are_refused(sql):
    assert not _read_only_statement(sql)


async def test_a_read_only_connection_refuses_writes_before_the_engine(monkeypatch):
    calls = []

    class Service:
        async def preflight_script(self, statements, **kwargs):
            return None

        async def execute_statements(self, **kwargs):
            calls.append(kwargs["sql"])
            from app.modules.query.repository import QueryResult

            return [QueryResult(columns=["n"], rows=[[1]])]

    audits = []

    async def audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr("app.proxy.executor.query_service", Service())
    monkeypatch.setattr("app.common.audit.write_audit_log", audit)
    executor = ProxyQueryExecutor(SessionState(read_only_connection=True, active_role="analyst"))
    refused = await executor.execute("DELETE FROM t", username="alice", connection=object())
    assert refused.error_code == 1792 and calls == []
    assert audits and audits[0]["status"] == "DENIED"
    read = await executor.execute("SELECT n FROM t", username="alice", connection=object())
    assert read.is_resultset and calls == ["SELECT n FROM t"]
