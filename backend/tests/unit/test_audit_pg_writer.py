"""Audit events go to the PostgreSQL audit log under the event's own identity.

A successful governed action inside the caller's metadata transaction is
recorded in that transaction; a refusal or failure gets its own transaction
so it survives the rollback of the refused change.
"""

from __future__ import annotations

import pytest

from app.common import audit
from app.common.audit import _persist as real_persist
from app.common.audit import audit_actor
from app.core.metadata_scope import metadata_transaction
from app.modules.access_control.security_context import SecurityContext


class Appended:
    def __init__(self) -> None:
        self.calls: list = []

    def __call__(self, connection):
        outer = self

        class Repository:
            async def append(self, identity, event):
                outer.calls.append((connection, identity, event))
                return {}

        return Repository()


@pytest.fixture
def appended(monkeypatch, fake_metadata_db):
    recorder = Appended()
    monkeypatch.setattr(audit, "AuditRepository", recorder)
    monkeypatch.setattr(audit, "_persist", real_persist)
    return recorder


ALICE = SecurityContext(principal="alice", active_role="ANALYST", security_context_version=3)


async def _write(status="SUCCESS", **changes):
    fields = dict(
        event_type="ONTOLOGY",
        user_name="alice",
        action="UPDATE",
        object_type="DOMAIN",
        object_name="sales",
        status=status,
        active_role="ANALYST",
        security_context_version=3,
    )
    return await audit.write_audit_log(**{**fields, **changes})


async def test_success_joins_the_open_transaction_for_the_same_actor(appended, fake_metadata_db):
    async with metadata_transaction(ALICE) as scope:
        await _write()
    assert len(fake_metadata_db.transactions) == 1
    assert appended.calls[0][0] is scope.connection
    assert fake_metadata_db.transactions[0].outcome == "committed"


async def test_success_audit_rolls_back_with_the_change(appended, fake_metadata_db):
    with pytest.raises(RuntimeError):
        async with metadata_transaction(ALICE):
            await _write()
            raise RuntimeError("constraint failed later")
    assert [item.outcome for item in fake_metadata_db.transactions] == ["rolled_back"]


async def test_refusal_survives_the_rollback_in_its_own_transaction(appended, fake_metadata_db):
    with pytest.raises(RuntimeError):
        async with metadata_transaction(ALICE):
            await _write(status="DENIED")
            raise RuntimeError("refused")
    outcomes = [item.outcome for item in fake_metadata_db.transactions]
    assert sorted(outcomes) == ["committed", "rolled_back"]
    own = next(item for item in fake_metadata_db.transactions if item.outcome == "committed")
    assert appended.calls[0][0] is own.connection
    assert own.actor.principal == "alice" and own.actor.active_role == "ANALYST"


async def test_another_actor_in_the_open_transaction_is_written_separately(
    appended,
    fake_metadata_db,
):
    async with metadata_transaction(ALICE):
        await _write(user_name="bob", active_role="ANALYST")
    assert len(fake_metadata_db.transactions) == 2
    assert fake_metadata_db.transactions[1].actor.principal == "bob"


async def test_without_a_transaction_each_event_commits_alone(appended, fake_metadata_db):
    query_id = await _write(query_id="1f0e4c1e-0000-4000-8000-000000000001")
    assert query_id == "1f0e4c1e-0000-4000-8000-000000000001"
    assert [item.outcome for item in fake_metadata_db.transactions] == ["committed"]
    identity, event = appended.calls[0][1], appended.calls[0][2]
    assert identity.event_time.tzinfo is not None
    assert event.query_id == query_id


async def test_another_login_does_not_join_the_open_transaction(appended, fake_metadata_db):
    actor = SecurityContext("alice", "ANALYST", session_id="login-a", security_context_version=3)
    async with metadata_transaction(actor):
        await _write(session_id="login-b")
    assert len(fake_metadata_db.transactions) == 2
    assert fake_metadata_db.transactions[1].actor.session_id == "login-b"


@pytest.mark.parametrize("principal_type", ["USER", "SERVICE"])
async def test_legacy_event_keeps_bound_login_version_and_type(
    appended, fake_metadata_db, principal_type
):
    actor = SecurityContext(
        "alice",
        "ANALYST",
        session_id="login-a",
        security_context_version=7,
        principal_type=principal_type,
    )
    async with metadata_transaction(actor) as scope:
        await _write(security_context_version=None)
    assert len(fake_metadata_db.transactions) == 1
    assert appended.calls[0][0] is scope.connection
    assert appended.calls[0][2].session_id == "login-a"


async def test_explicit_stale_version_is_not_replaced_by_the_bound_version(
    appended, fake_metadata_db
):
    async with metadata_transaction(ALICE):
        await _write(security_context_version=2)
    assert len(fake_metadata_db.transactions) == 2
    assert fake_metadata_db.transactions[1].actor.security_context_version == 2


def test_role_less_and_root_events_have_an_explicit_identity():
    signin = audit_actor("carol", None)
    assert (signin.principal, signin.active_role, signin.principal_type) == (
        "carol",
        audit.NO_ACTIVE_ROLE,
        "USER",
    )
    root = audit_actor("root", None)
    assert root.principal_type == "SERVICE"
    assert audit_actor("", "ALL").principal == audit.ANONYMOUS
    assert audit_actor("dave", "a,b").active_role == audit.NO_ACTIVE_ROLE
    assert audit_actor("erin", "R", 0, "x" * 65).session_id is None


async def test_text_is_redacted_and_storable(audit_events):
    await _write(
        object_name="",
        sql_text="SELECT 1 /* \0 */ FROM FILES('aws.s3.secret_key'='SECRET_X')",
    )
    _, event, success = audit_events[0]
    assert success is True
    assert event.object_name == "-"
    assert "\0" not in event.sql_text and "SECRET_X" not in event.sql_text


async def test_execution_telemetry_is_kept(audit_events):
    from app.modules.query_autopilot import telemetry

    class Execution:
        id = "exec-1"
        query_ids = ["q-1"]
        audit_ids: list = []

    token = telemetry.EXECUTION.set(Execution())
    try:
        qid = await _write()
    finally:
        telemetry.EXECUTION.reset(token)
    _, event, _ = audit_events[0]
    assert event.nova_execution_id == "exec-1"
    assert event.engine_query_ids == ("q-1",)
    assert Execution.audit_ids == [qid]
