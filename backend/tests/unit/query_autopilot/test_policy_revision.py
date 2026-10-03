from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.integrations.ranger.client import RangerClient, ranger_client
from app.modules.query_autopilot.models import Scope
from app.modules.query_autopilot.runtime import (
    AuthorizationUnavailable,
    AuthorizedSQL,
    EvidenceUnavailable,
    validate_policy_revision,
)


@pytest.mark.parametrize(
    "metadata,expected",
    [
        ({"version": 3, "policyVersion": 58, "tagVersion": 2}, "3:58:2"),
        ({"version": 3, "policyVersion": 58, "tagVersion": 3}, "3:58:3"),
        ({"policyVersion": 0}, "unknown:0:unknown"),
        ({"version": 3}, None),
        ({"policyVersion": True}, None),
        ({"policyVersion": -1}, None),
        ({"policyVersion": "58"}, None),
    ],
)
async def test_revision_uses_only_available_control_plane_counters(metadata, expected):
    client = RangerClient()
    client.get_service = AsyncMock(return_value={**metadata, "configs": {"password": "private"}})
    value = await client.policy_revision()
    assert value == expected
    assert value is None or "private" not in value


@pytest.mark.parametrize("recorded,current,blocked", [
    ("3:58:2", "3:58:2", False),
    ("3:58:2", "3:59:2", True),
    (None, "3:58:2", True),
    (None, None, False),
])
async def test_changed_or_newly_available_revision_invalidates_scope(
    monkeypatch, recorded, current, blocked,
):
    monkeypatch.setattr(settings, "RANGER_ENABLED", True)
    monkeypatch.setattr(ranger_client, "policy_revision", AsyncMock(return_value=current))
    scope = Scope(
        principal="alice", active_role="analyst", security_context_version=1,
        policy_revision=recorded,
    )
    if blocked:
        with pytest.raises(AuthorizationUnavailable, match="ranger_policy_revision_changed"):
            await validate_policy_revision(scope)
    else:
        await validate_policy_revision(scope)


async def test_metadata_outage_blocks_delegated_statement_before_engine_execution(monkeypatch):
    from app.modules.query.service import query_service

    monkeypatch.setattr(settings, "RANGER_ENABLED", True)
    monkeypatch.setattr(ranger_client, "policy_revision", AsyncMock(side_effect=RuntimeError()))
    execute = AsyncMock()
    monkeypatch.setattr(query_service, "execute", execute)
    scope = Scope(principal="alice", active_role="analyst", security_context_version=1)
    with pytest.raises(AuthorizationUnavailable, match="ranger_policy_revision_unavailable"):
        await AuthorizedSQL().execute(
            "SELECT 1", scope, connection=object(), category="experiment",
        )
    execute.assert_not_awaited()


@pytest.mark.parametrize("code,refused", [
    (1044, True), (1045, True), (1142, True), (1143, True), (5203, True), (5204, True),
    (1064, False), (2003, False),
])
async def test_engine_authorization_codes_remain_distinct_from_operational_outages(
    monkeypatch, code, refused,
):
    from asyncmy.errors import OperationalError

    from app.core.exceptions import StarRocksError
    from app.modules.query.service import query_service

    monkeypatch.setattr(settings, "RANGER_ENABLED", False)
    failure = StarRocksError("redacted engine refusal")
    failure.__cause__ = OperationalError(code, "fixture failure")
    execute = AsyncMock(side_effect=failure)
    monkeypatch.setattr(query_service, "execute", execute)
    scope = Scope(principal="alice", active_role="analyst", security_context_version=1)
    with pytest.raises(AuthorizationUnavailable if refused else EvidenceUnavailable):
        await AuthorizedSQL().execute(
            "SELECT COUNT(*) FROM sales", scope, connection=object(), category="experiment",
        )
    execute.assert_awaited_once()
