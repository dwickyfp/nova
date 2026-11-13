"""Semantic publication rechecks the candidate and commits with its audit."""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.metadata_db import MetadataConflict
from app.core.metadata_scope import current_scope
from app.modules.intelligence import semantic_views
from app.modules.intelligence.metadata_repository import IntelligenceMetadataRepository

USER = {
    "username": "owner",
    "active_role": "ANALYST",
    "encrypted_password": "opaque",
    "session_id": "caller-session",
    "security_context_version": 7,
    "roles": ["ANALYST"],
}


@pytest.fixture
def publication(monkeypatch, fake_metadata_db):
    from app.core.redis import session_store
    from app.modules.intelligence.metadata_repository import intelligence_metadata

    service = semantic_views.SemanticViewService()
    view = {
        "name": "sales",
        "database_name": "sales_db",
        "active_version": 1,
        "owner_name": "owner",
        "metadata_revision": 4,
    }
    version = {
        "status": "VALIDATED",
        "fingerprint": "candidate",
        "definition": {},
        "validation": {"valid": True, "baseline_version": 1, "regression": {"changed": 0}},
    }
    inside = False
    source_checked = False
    repo = AsyncMock(spec=IntelligenceMetadataRepository)
    repo.get_view.side_effect = lambda *_: deepcopy(view)
    repo.get_version.side_effect = lambda *_: deepcopy(version)

    @asynccontextmanager
    async def transaction(actor, **kwargs):
        nonlocal inside
        assert source_checked
        inside = True
        try:
            async with intelligence_metadata(actor, **kwargs):
                yield repo
        finally:
            inside = False

    async def probe(_definition, caller):
        nonlocal source_checked
        assert not inside and caller is USER
        source_checked = True
        return True

    async def publish(*args, **kwargs):
        assert inside and current_scope().actor.session_id == USER["session_id"]

    async def audit(*args):
        assert inside and current_scope().actor.security_context_version == 7
        repo.publish.assert_awaited_once()

    async def after_commit(*args):
        assert not inside
        return []

    repo.publish.side_effect = publish
    checkpoint = AsyncMock(return_value=SimpleNamespace(error=None))
    monkeypatch.setattr(service, "_owned", AsyncMock(side_effect=lambda *_: deepcopy(view)))
    monkeypatch.setattr(service, "_version", AsyncMock(side_effect=lambda *_: deepcopy(version)))
    monkeypatch.setattr(service, "_source_access", probe)
    monkeypatch.setattr(service, "describe", AsyncMock(return_value={"active_version": 2}))
    monkeypatch.setattr(service, "_audit", AsyncMock(side_effect=audit))
    monkeypatch.setattr(service, "sync_bi", AsyncMock(side_effect=after_commit))
    monkeypatch.setattr(semantic_views, "_relink", AsyncMock(side_effect=after_commit))
    monkeypatch.setattr(semantic_views, "intelligence_metadata", transaction)
    monkeypatch.setattr(semantic_views.query_service, "execute", checkpoint)
    monkeypatch.setattr(session_store, "get", AsyncMock(return_value=deepcopy(USER)))
    monkeypatch.setattr(
        "app.modules.ontology.representations.refresh_after_publish",
        AsyncMock(side_effect=after_commit),
    )
    return SimpleNamespace(
        service=service,
        view=view,
        version=version,
        repo=repo,
        checkpoint=checkpoint,
        database=fake_metadata_db,
    )


async def test_probes_precede_the_transaction_and_bi_follows_commit(publication):
    p = publication
    assert await p.service.publish("view", 2, USER) == {"active_version": 2, "bi_datasets": []}
    p.repo.publish.assert_awaited_once_with("view", 2, expected_revision=4, expected_active=1)
    p.service._audit.assert_awaited_once_with("PUBLISH", "sales", USER)
    p.service.sync_bi.assert_awaited_once_with("view", USER)
    p.checkpoint.assert_awaited_once_with(
        sql="SELECT 1",
        username="owner",
        encrypted_password="opaque",
        database="sales_db",
        role="ANALYST",
        session_id="caller-session",
        security_context_version=7,
        max_rows=0,
    )
    assert [tx.outcome for tx in p.database.transactions] == ["committed"]


@pytest.mark.parametrize("changed", ["definition", "fingerprint", "validation", "baseline"])
async def test_inputs_changed_during_probes_are_rejected(publication, changed):
    p = publication

    async def change(**kwargs):
        if changed == "baseline":
            p.view["active_version"] = 3
        elif changed == "validation":
            p.version["validation"]["regression"]["changed"] = 1
        elif changed == "definition":
            p.version["definition"]["changed"] = True
        else:
            p.version["fingerprint"] = "replacement"
        return SimpleNamespace(error=None)

    p.checkpoint.side_effect = change
    with pytest.raises(HTTPException) as exc:
        await p.service.publish("view", 2, USER, acknowledge_regressions=True)
    assert exc.value.status_code == 409
    p.repo.publish.assert_not_awaited()
    p.service.sync_bi.assert_not_awaited()


async def test_expired_execution_context_cannot_start_a_write(publication):
    p = publication
    p.checkpoint.return_value = SimpleNamespace(error="context revoked")
    with pytest.raises(HTTPException) as exc:
        await p.service.publish("view", 2, USER)
    assert exc.value.status_code == 403
    assert p.database.transactions == []
    p.repo.publish.assert_not_awaited()


@pytest.mark.parametrize(
    "changed",
    [
        None,
        {"active_role": "OTHER", "roles": ["OTHER"]},
        {"security_context_version": 8},
        {"username": "other"},
    ],
)
async def test_session_is_revalidated_before_the_write_transaction(
    publication, monkeypatch, changed
):
    from app.core.redis import session_store

    p = publication
    monkeypatch.setattr(
        session_store, "get", AsyncMock(return_value={**USER, **changed} if changed else None)
    )
    with pytest.raises(HTTPException) as exc:
        await p.service.publish("view", 2, USER)
    assert exc.value.status_code == 403
    assert p.database.transactions == []
    p.repo.publish.assert_not_awaited()


@pytest.mark.parametrize("boundary", ["cas", "publish", "audit"])
async def test_a_failure_rolls_back_the_unit_without_publishing_bi(publication, boundary):
    p = publication
    failure = MetadataConflict() if boundary == "cas" else RuntimeError("fixture failure")
    if boundary == "audit":
        p.service._audit.side_effect = failure
    else:
        p.repo.publish.side_effect = failure
    with pytest.raises(type(failure)):
        await p.service.publish("view", 2, USER)
    assert [tx.outcome for tx in p.database.transactions] == ["rolled_back"]
    p.service.sync_bi.assert_not_awaited()
    semantic_views._relink.assert_not_awaited()


async def test_recovery_cannot_replace_a_newer_publication(publication):
    p = publication
    p.view["active_version"] = 3
    p.version["status"] = "ACTIVE"
    with pytest.raises(HTTPException) as exc:
        await p.service.publish("view", 2, USER)
    assert exc.value.status_code == 409
    p.repo.publish.assert_not_awaited()
    p.checkpoint.assert_not_awaited()
