from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from app.common.metadata_actors import metadata_scope
from app.core import metadata_actor
from app.core import metadata_scope as scopes
from app.core.metadata_db import MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext

ACTOR = SecurityContext("alice", "ANALYST", session_id="login-1", security_context_version=3)


@pytest.fixture
def database(monkeypatch):
    calls = []

    class Database:
        @asynccontextmanager
        async def transaction(self, actor, *, read_only=False):
            connection = object()
            calls.append((actor, read_only, connection))
            yield connection

    monkeypatch.setattr(scopes, "metadata_db", Database)
    scopes.clear_actor()
    yield calls
    scopes.clear_actor()


async def test_domain_adapters_share_one_actor_and_transaction(database):
    with metadata_actor.acting_as(ACTOR):
        async with scopes.metadata_transaction(ACTOR) as outer:
            async with metadata_actor.metadata_transaction(read_only=True) as (conn, actor):
                assert conn is outer.connection
                assert actor == ACTOR
            async with metadata_scope(ACTOR, connection=outer.connection) as conn:
                assert conn is outer.connection
    assert len(database) == 1
    assert metadata_actor.bound_actor() is None


@pytest.mark.parametrize("changed", [
    {"principal": "bob"}, {"active_role": "ACCOUNTADMIN"},
    {"session_id": "login-2"}, {"security_context_version": 4},
    {"principal_type": "SERVICE"},
])
async def test_nested_transaction_cannot_change_identity(database, changed):
    async with scopes.metadata_transaction(ACTOR):
        with pytest.raises(ValueError):
            async with scopes.metadata_transaction(replace(ACTOR, **changed)):
                pytest.fail("a different actor joined the transaction")
    assert len(database) == 1


async def test_child_inherits_actor_but_never_parent_connection(database):
    with metadata_actor.acting_as(ACTOR):
        async with scopes.metadata_transaction(ACTOR) as parent:
            async def child():
                assert scopes.active_scope() is None
                with pytest.raises(MetadataPermissionDenied):
                    async with metadata_scope(ACTOR, connection=parent.connection):
                        pytest.fail("child reused the parent's connection")
                async with metadata_actor.metadata_transaction() as (connection, actor):
                    assert connection is not parent.connection
                    assert actor == ACTOR
            await asyncio.create_task(child())
    assert len(database) == 2


async def test_read_only_transaction_cannot_be_escalated(database):
    async with scopes.metadata_transaction(ACTOR, read_only=True):
        with pytest.raises(ValueError):
            async with metadata_actor.metadata_transaction(ACTOR):
                pytest.fail("read-only transaction became writable")


async def test_declared_actor_conflict_is_refused(database):
    async with scopes.metadata_transaction(ACTOR):
        with metadata_actor.acting_as(replace(ACTOR, session_id="another-login")):
            with pytest.raises(ValueError):
                async with scopes.scoped_transaction():
                    pytest.fail("different login used the open scope")


async def test_actor_restored_on_cancellation(database):
    with metadata_actor.acting_as(ACTOR):
        with pytest.raises(asyncio.CancelledError):
            async with scopes.acting_as(replace(ACTOR, active_role="PUBLIC")):
                raise asyncio.CancelledError()
        assert metadata_actor.current_actor() == ACTOR
    assert scopes.acting_actor() is None


def test_invalid_request_clears_previous_actor(database):
    user = {"username": "alice", "active_role": "ANALYST", "session_id": "login-1",
            "security_context_version": 3, "assigned_roles": ["ANALYST"]}
    assert metadata_actor.bind_request_actor(user) == ACTOR
    assert metadata_actor.bind_request_actor({"username": "bob"}) is None
    with pytest.raises(MetadataPermissionDenied):
        metadata_actor.current_actor()
