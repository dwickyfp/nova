"""Preview admission stays outside the lease; publication rechecks live state."""

import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.intelligence import actions, decisions
from app.modules.intelligence.action_contracts import ActionOperation, ActionReview
from app.modules.intelligence.contracts import fingerprint
from tests.unit.test_business_actions import action_program as _action_program
from tests.unit.test_business_actions import program as _program
from tests.unit.test_intelligence_engine import USER

program = _program
action_program = _action_program


class PreviewLease:
    def __init__(self):
        self.owner = ContextVar("preview_lease_owner", default=None)
        self.locks = {}
        self.entries = []
        self.timeouts = []
        self.trace = []
        self.on_acquire = None
        self.valid = True
        self.renews = 0

    @property
    def held(self):
        return self.owner.get() is not None

    def record(self, operation):
        self.trace.append((operation, self.held))

    @asynccontextmanager
    async def lock(self, key, *, timeout_seconds=20):
        async with self.locks.setdefault(key, asyncio.Lock()):
            token = self.owner.set(key)
            self.entries.append(key)
            self.timeouts.append(timeout_seconds)
            try:
                if self.on_acquire:
                    self.on_acquire()
                yield self
            finally:
                self.owner.reset(token)

    async def renew(self):
        assert self.held
        self.record("renew")
        self.renews += 1
        return self.valid


def action_metadata(repo):
    return deepcopy(
        {key: row for key, row in repo.rows.items() if key[0] in {"actions", "action_events"}}
    )


@pytest.fixture
async def preview_boundary(action_program, monkeypatch):
    service, action, request, adapter, _, policy, repo, source = action_program
    lease = PreviewLease()
    sessions = {USER["session_id"]: deepcopy(USER)}
    metadata_before = action_metadata(repo)
    original_get, original_save = repo.get, repo.save
    original_query, original_access = source.execute_plan, source._readable_version
    original_preview, original_audit = adapter.preview, service.service._audit

    async def get(kind, *args, **kwargs):
        lease.record("get:" + kind)
        return await original_get(kind, *args, **kwargs)

    async def save(kind, *args, **kwargs):
        lease.record("save:" + kind)
        return await original_save(kind, *args, **kwargs)

    async def query(*args, **kwargs):
        lease.record("data_query")
        return await original_query(*args, **kwargs)

    async def access(*args, **kwargs):
        lease.record("source_access")
        return await original_access(*args, **kwargs)

    async def preview(*args, **kwargs):
        lease.record("adapter_preview")
        return await original_preview(*args, **kwargs)

    async def audit(*args, **kwargs):
        lease.record("audit")
        return await original_audit(*args, **kwargs)

    async def session(ident):
        lease.record("session")
        return deepcopy(sessions.get(ident))

    async def read_policy():
        lease.record("policy")
        return policy.model_copy(deep=True)

    get_mock, save_mock = AsyncMock(side_effect=get), AsyncMock(side_effect=save)
    query_mock, access_mock = AsyncMock(side_effect=query), AsyncMock(side_effect=access)
    preview_mock, audit_mock = AsyncMock(side_effect=preview), AsyncMock(side_effect=audit)
    monkeypatch.setattr(repo, "get", get_mock)
    monkeypatch.setattr(repo, "save", save_mock)
    monkeypatch.setattr(source, "execute_plan", query_mock)
    monkeypatch.setattr(source, "_readable_version", access_mock)
    monkeypatch.setattr(adapter, "preview", preview_mock)
    monkeypatch.setattr(service.service, "_audit", audit_mock)
    monkeypatch.setattr(actions, "metadata_lock", lease.lock)
    monkeypatch.setattr(actions.session_store, "get", AsyncMock(side_effect=session))
    monkeypatch.setattr(actions, "read_business_policy", AsyncMock(side_effect=read_policy))
    monkeypatch.setattr(decisions, "read_business_policy", AsyncMock(side_effect=read_policy))
    source.calls.clear()
    actions.write_audit_log.reset_mock()
    return SimpleNamespace(
        service=service,
        action=action,
        original_request=request,
        request=request.model_copy(update={"idempotency_key": "boundary-preview-1"}, deep=True),
        adapter=adapter,
        policy=policy,
        repo=repo,
        source=source,
        lease=lease,
        sessions=sessions,
        metadata_before=metadata_before,
        effects_before=deepcopy(adapter.effects),
        save=save_mock,
        query=query_mock,
        access=access_mock,
        preview=preview_mock,
        audit=audit_mock,
        denied_audit=actions.write_audit_log,
    )


def assert_no_publication(state):
    assert action_metadata(state.repo) == state.metadata_before
    state.save.assert_not_awaited()
    state.audit.assert_not_awaited()
    assert state.adapter.effects == state.effects_before


def assert_preview_lease_boundary(state):
    for operation, held in state.lease.trace:
        if operation in {"data_query", "adapter_preview"}:
            assert not held, operation
        if operation in {"save:actions", "save:action_events", "audit", "renew"}:
            assert held, operation
    assert not state.lease.held


def assert_guard_denied(state):
    assert_no_publication(state)
    state.preview.assert_not_awaited()
    state.denied_audit.assert_awaited_once()
    assert state.denied_audit.await_args.kwargs["status"] == "DENIED"
    assert state.denied_audit.await_args.kwargs["action"] == "EXECUTE_ACTION"
    assert state.denied_audit.await_args.kwargs["object_name"] == state.action.id


def reset_boundary_observations(state):
    state.metadata_before = action_metadata(state.repo)
    state.effects_before = deepcopy(state.adapter.effects)
    state.source.calls.clear()
    for mock in (
        state.save,
        state.query,
        state.access,
        state.preview,
        state.audit,
        state.denied_audit,
    ):
        mock.reset_mock()
    state.lease.trace.clear()
    state.lease.entries.clear()
    state.lease.timeouts.clear()
    state.lease.renews = 0


async def test_preview_runs_queries_and_adapter_outside_lease_and_publishes_inside(
    preview_boundary,
):
    state = preview_boundary
    saved = await state.service.preview(state.request, USER)

    assert saved.request_digest == fingerprint(state.request.model_dump(mode="json"))
    assert state.query.await_count > 0
    assert all(user == USER for _, user in state.source.calls)
    state.preview.assert_awaited_once_with(state.request.configuration, USER)
    state.audit.assert_awaited_once_with("PREVIEW_ACTION", saved, USER)
    assert Counter(call.args[0] for call in state.save.await_args_list) == {
        "actions": 1,
        "action_events": 1,
    }
    assert [held for operation, held in state.lease.trace if operation == "get:actions"] == [
        False,
        True,
    ]
    assert [held for operation, held in state.lease.trace if operation == "policy"] == [
        False,
        True,
    ]
    assert ("session", True) in state.lease.trace
    assert ("get:decisions", True) in state.lease.trace
    assert_preview_lease_boundary(state)
    assert state.lease.entries == ["action-preview:" + saved.id]
    assert state.lease.timeouts == [20]
    assert state.lease.renews == 1


@pytest.mark.parametrize("changed_payload", [False, True], ids=["same-inputs", "changed-inputs"])
async def test_concurrent_preview_returns_winner_or_conflicts_without_duplicate_writes(
    preview_boundary,
    monkeypatch,
    changed_payload,
):
    state = preview_boundary
    waiting, release = asyncio.Event(), asyncio.Event()
    original_preview = state.adapter.preview
    calls = 0

    async def pause_first(configuration, user):
        nonlocal calls
        calls += 1
        await original_preview(configuration, user)
        if calls == 1:
            waiting.set()
            await release.wait()

    monkeypatch.setattr(state.adapter, "preview", pause_first)
    winning_request = state.request.model_copy(deep=True)
    if changed_payload:
        winning_request.configuration.name = "Concurrent winner"
    first = asyncio.create_task(state.service.preview(state.request, USER))
    try:
        await asyncio.wait_for(waiting.wait(), timeout=5)
        winner = await asyncio.wait_for(state.service.preview(winning_request, USER), timeout=5)
        release.set()
        if changed_payload:
            with pytest.raises(HTTPException) as error:
                await asyncio.wait_for(first, timeout=5)
            assert error.value.status_code == 409
            assert error.value.detail == "Idempotency key inputs changed"
        else:
            assert await asyncio.wait_for(first, timeout=5) == winner
    finally:
        release.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)

    assert calls == 2
    assert state.repo.rows[("actions", winner.id)] == winner
    assert winner.configuration == winning_request.configuration
    assert Counter(call.args[0] for call in state.save.await_args_list) == {
        "actions": 1,
        "action_events": 1,
    }
    assert len(state.lease.entries) == 2
    state.audit.assert_awaited_once_with("PREVIEW_ACTION", winner, USER)
    assert_preview_lease_boundary(state)
    assert not state.adapter.effects


@pytest.mark.parametrize("changed_payload", [False, True], ids=["retry", "conflict"])
async def test_existing_preview_checks_digest_without_lease_or_adapter(
    preview_boundary,
    changed_payload,
):
    state = preview_boundary
    request = state.original_request.model_copy(deep=True)
    if changed_payload:
        request.configuration.name = "Changed retry"
        with pytest.raises(HTTPException) as error:
            await state.service.preview(request, USER)
        assert error.value.status_code == 409
        state.query.assert_not_awaited()
    else:
        assert await state.service.preview(request, USER) == state.action
        assert state.query.await_count > 0
    assert not state.lease.entries
    state.preview.assert_not_awaited()
    assert_no_publication(state)


@pytest.mark.parametrize("change", ["missing", "revision", "digest"])
async def test_decision_change_between_admission_and_lease_prevents_publication(
    preview_boundary,
    change,
):
    state = preview_boundary

    def change_decision():
        key = ("decisions", state.request.decision_id)
        if change == "missing":
            del state.repo.rows[key]
        elif change == "revision":
            state.repo.rows[key].revision += 1
        else:
            state.repo.rows[key].title = "Changed without a revision bump"

    state.lease.on_acquire = change_decision
    with pytest.raises(HTTPException) as error:
        await state.service.preview(state.request, USER)
    assert error.value.status_code == 409
    assert error.value.detail == "Decision changed during preview"
    assert state.query.await_count > 0
    state.preview.assert_awaited_once()
    assert_no_publication(state)


@pytest.mark.parametrize("change", ["revision", "blocked-action", "reviewer-roles"])
async def test_policy_change_after_evaluation_prevents_publication(preview_boundary, change):
    state = preview_boundary

    def change_policy():
        if change == "revision":
            state.policy.revision += 1
        elif change == "blocked-action":
            state.policy.blocked_actions = ["monitor"]
        else:
            state.policy.reviewer_roles = ["ACCOUNTADMIN"]

    state.lease.on_acquire = change_policy
    with pytest.raises(HTTPException) as error:
        await state.service.preview(state.request, USER)
    assert error.value.status_code == 409
    assert error.value.detail == "Action policy changed during preview"
    assert [held for operation, held in state.lease.trace if operation == "policy"] == [
        False,
        True,
    ]
    assert_no_publication(state)


SESSION_CHANGES = [
    pytest.param(None, id="expired"),
    pytest.param({"username": "bob"}, id="principal"),
    pytest.param({"active_role": "VIEWER", "roles": ["VIEWER"]}, id="active-role"),
    pytest.param({"security_context_version": 2}, id="security-version"),
]


@pytest.mark.parametrize("change", SESSION_CHANGES)
async def test_session_change_at_lease_entry_prevents_publication(preview_boundary, change):
    state = preview_boundary

    def change_session():
        state.sessions[USER["session_id"]] = None if change is None else {**USER, **change}

    state.lease.on_acquire = change_session
    with pytest.raises(HTTPException) as error:
        await state.service.preview(state.request, USER)
    assert error.value.status_code == 403
    assert error.value.detail == "Session or active role changed"
    assert state.query.await_count > 0
    state.preview.assert_awaited_once()
    assert ("session", True) in state.lease.trace
    assert_no_publication(state)


async def test_expired_preview_lease_prevents_all_publication(preview_boundary):
    state = preview_boundary
    state.lease.valid = False
    with pytest.raises(HTTPException) as error:
        await state.service.preview(state.request, USER)
    assert error.value.status_code == 409
    assert error.value.detail == "Action preview lease expired"
    assert state.lease.renews == 1
    assert_no_publication(state)


@pytest.mark.parametrize("change, status", [("masked", 409), ("revoked", 404)])
async def test_preview_admission_still_validates_live_evidence(preview_boundary, change, status):
    state = preview_boundary
    setattr(state.source, change, True)
    with pytest.raises(HTTPException) as error:
        await state.service.preview(state.request, USER)
    assert error.value.status_code == status
    assert not state.lease.entries
    state.preview.assert_not_awaited()
    assert_no_publication(state)


async def test_each_guard_revalidates_admitted_evidence_with_live_caller(
    preview_boundary,
    monkeypatch,
):
    state = preview_boundary
    admitted = await state.service.get(state.action.id, USER)
    assert state.query.await_count > 0
    state.query.reset_mock()
    state.access.reset_mock()
    full_get = AsyncMock(wraps=state.service.service.get)
    monkeypatch.setattr(state.service.service, "get", full_get)

    await state.service._preconditions(admitted, USER)
    first_queries = state.query.await_count
    assert first_queries > 0
    await state.service._preconditions(admitted, USER)

    assert full_get.await_count == 2
    for call in full_get.await_args_list:
        assert call.args == ("decisions", admitted.decision_id, USER)
    assert state.query.await_count > first_queries
    assert all(user == USER for _, user in state.source.calls)
    assert state.access.await_count >= 2
    for call in state.access.await_args_list:
        assert call.args == (admitted.semantic.view_id, admitted.semantic.version, USER)
    assert state.preview.await_count == 2
    state.denied_audit.assert_not_awaited()
    assert_no_publication(state)


@pytest.mark.parametrize("change", ["missing", "revision", "digest", "scope"])
async def test_guard_rejects_changed_durable_decision_proof(preview_boundary, change):
    state = preview_boundary
    decision = state.repo.rows[("decisions", state.action.decision_id)]
    key = ("events", fingerprint([decision.id, decision.last_operation_id]))
    if change == "missing":
        del state.repo.rows[key]
    elif change == "revision":
        state.repo.rows[key].decision_revision += 1
    elif change == "digest":
        state.repo.rows[key].context_digest = "0" * 64
    else:
        state.repo.rows[key].scope = decision.scope.model_copy(update={"principal": "bob"})

    with pytest.raises(HTTPException) as error:
        await state.service._preconditions(state.action, USER)
    assert error.value.status_code == 409
    assert error.value.detail == "Decision lineage is incomplete; retry the operation"
    assert state.query.await_count > 0
    assert_guard_denied(state)


@pytest.mark.parametrize("change", ["revision", "digest"])
async def test_guard_rejects_changed_decision_even_with_consistent_current_proof(
    preview_boundary,
    change,
):
    state = preview_boundary
    decision = state.repo.rows[("decisions", state.action.decision_id)]
    if change == "revision":
        decision.revision += 1
    else:
        decision.title = "A different approved decision payload"
    proof = state.repo.rows[("events", fingerprint([decision.id, decision.last_operation_id]))]
    proof.decision_revision = decision.revision
    proof.context_digest = decisions.decision_digest(decision)

    with pytest.raises(HTTPException) as error:
        await state.service._preconditions(state.action, USER)
    assert error.value.status_code == 409
    assert error.value.detail == "Decision changed; preview a new action"
    assert state.query.await_count > 0
    assert_guard_denied(state)


@pytest.mark.parametrize("change", SESSION_CHANGES)
async def test_guard_revalidates_live_session(preview_boundary, change):
    state = preview_boundary
    state.sessions[USER["session_id"]] = None if change is None else {**USER, **change}
    with pytest.raises(HTTPException) as error:
        await state.service._preconditions(state.action, USER)
    assert error.value.status_code == 403
    state.access.assert_not_awaited()
    state.query.assert_not_awaited()
    assert_guard_denied(state)


@pytest.mark.parametrize("change, status", [("revoked", 404), ("active-version", 409)])
async def test_guard_rechecks_live_source_access(preview_boundary, change, status):
    state = preview_boundary
    if change == "revoked":
        state.source.revoked = True
    else:
        state.source.active_version += 1
    with pytest.raises(HTTPException) as error:
        await state.service._preconditions(state.action, USER)
    assert error.value.status_code == status
    assert state.access.await_count > 0
    for call in state.access.await_args_list:
        assert call.args == (state.action.semantic.view_id, state.action.semantic.version, USER)
    if change == "revoked":
        state.query.assert_not_awaited()
    else:
        assert state.query.await_count > 0
    assert_guard_denied(state)


@pytest.mark.parametrize("operation", ["dispatch", "verify"])
async def test_mask_change_after_action_admission_blocks_effect_or_verified_publication(
    preview_boundary,
    monkeypatch,
    operation,
):
    state = preview_boundary
    if operation == "verify":
        state.adapter.complete = False
        state.action = await state.service.dispatch(
            state.action.id,
            ActionOperation(
                operation_id="prepare-readback-boundary",
                expected_revision=state.action.revision,
                thread_id="thread",
            ),
            USER,
        )
        assert state.action.status == "verification_required"
        assert len(state.adapter.effects) == 1
        state.adapter.complete = True
        reset_boundary_observations(state)

    execute = AsyncMock(wraps=state.adapter.execute)
    verify = AsyncMock(wraps=state.adapter.verify)
    monkeypatch.setattr(state.adapter, "execute", execute)
    monkeypatch.setattr(state.adapter, "verify", verify)

    def mask_after_admission():
        assert state.query.await_count > 0
        assert all(not held for op, held in state.lease.trace if op == "data_query")
        assert all(user == USER for _, user in state.source.calls)
        state.source.masked = True

    state.lease.on_acquire = mask_after_admission
    with pytest.raises(HTTPException) as error:
        if operation == "dispatch":
            await state.service.dispatch(
                state.action.id,
                ActionOperation(
                    operation_id="dispatch-after-mask-change",
                    expected_revision=state.action.revision,
                    thread_id="thread",
                ),
                USER,
            )
        else:
            await state.service.verify(
                state.action.id,
                USER,
                expected_revision=state.action.revision,
            )

    assert error.value.status_code == 409
    assert error.value.detail == "Evidence changed; refresh the investigation"
    assert ("data_query", True) in state.lease.trace
    assert state.lease.entries == ["action-dispatch:" + state.action.id]
    assert state.lease.timeouts == [120]
    execute.assert_not_awaited()
    verify.assert_not_awaited()
    assert_guard_denied(state)


async def test_mask_change_during_readback_blocks_verified_publication(
    preview_boundary,
    monkeypatch,
):
    state = preview_boundary
    state.adapter.complete = False
    state.action = await state.service.dispatch(
        state.action.id,
        ActionOperation(
            operation_id="prepare-mid-readback-mask",
            expected_revision=state.action.revision,
            thread_id="thread",
        ),
        USER,
    )
    assert state.action.status == "verification_required"
    state.adapter.complete = True
    reset_boundary_observations(state)
    original_verify = state.adapter.verify
    queries_before_mask = []

    async def mask_during_readback(*args, **kwargs):
        result = await original_verify(*args, **kwargs)
        assert result.complete
        assert state.lease.held
        queries_before_mask.append(state.query.await_count)
        state.source.masked = True
        return result

    verify = AsyncMock(side_effect=mask_during_readback)
    monkeypatch.setattr(state.adapter, "verify", verify)
    with pytest.raises(HTTPException) as error:
        await state.service.verify(state.action.id, USER, expected_revision=state.action.revision)

    assert error.value.status_code == 409
    assert error.value.detail == "Evidence changed; refresh the investigation"
    verify.assert_awaited_once()
    assert queries_before_mask[0] > 0
    assert state.query.await_count > queries_before_mask[0]
    assert state.lease.timeouts == [120]
    assert state.repo.rows[("actions", state.action.id)].status == "verification_required"
    assert_no_publication(state)
    state.denied_audit.assert_awaited_once()
    assert state.denied_audit.await_args.kwargs["status"] == "DENIED"


@pytest.mark.parametrize("revoke", [False, True], ids=["live-share", "revoked-share"])
async def test_cross_principal_review_rechecks_share_grant_before_approval(
    preview_boundary,
    monkeypatch,
    revoke,
):
    state = preview_boundary
    state.action = await state.service.preview(state.request, USER)
    assert state.action.status == "awaiting_approval"
    assert state.action.approval is None
    reset_boundary_observations(state)
    bob = {**USER, "username": "bob", "session_id": "session-bob"}
    state.sessions[bob["session_id"]] = deepcopy(bob)
    grant_live = True
    grant_checks = []

    async def share_grant(decision_id, user, owner=None):
        assert decision_id == state.action.decision_id
        assert user == bob
        assert owner in {None, USER["username"]}
        grant_checks.append((grant_live, state.lease.held))
        return {"owner_name": USER["username"]} if grant_live else None

    monkeypatch.setattr(
        state.service.service, "_decision_grant", AsyncMock(side_effect=share_grant)
    )

    def change_share_after_admission():
        nonlocal grant_live
        assert state.query.await_count > 0
        assert all(user == bob for _, user in state.source.calls)
        assert grant_checks == [(True, False), (True, False)]
        grant_live = not revoke

    state.lease.on_acquire = change_share_after_admission
    body = ActionReview(
        operation_id="bob-review-after-admission",
        expected_revision=state.action.revision,
        operation="approve",
    )
    if revoke:
        with pytest.raises(HTTPException) as error:
            await state.service.review(state.action.id, body, bob)
        assert error.value.status_code == 404
        assert error.value.detail == "Record unavailable"
        assert grant_checks[-1] == (False, True)
        assert_no_publication(state)
        current = state.repo.rows[("actions", state.action.id)]
        assert current.status == "awaiting_approval"
        assert current.approval is None
    else:
        result = await state.service.review(state.action.id, body, bob)
        assert result["status"] == "approved"
        assert result["revision"] == state.action.revision + 1
        approved = state.repo.rows[("actions", state.action.id)]
        assert approved.approval.actor == bob["username"]
        assert approved.approval.session_id == bob["session_id"]
        assert grant_checks[-2:] == [(True, True), (True, True)]
        state.audit.assert_awaited_once_with("ACTION_APPROVED", approved, bob)
    assert state.lease.entries == ["action-dispatch:" + state.action.id]
    assert state.lease.timeouts == [120]
