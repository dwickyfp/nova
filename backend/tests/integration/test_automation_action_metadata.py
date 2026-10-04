"""Studio automation identity, typed binding, and compensation persist in StarRocks."""

from uuid import uuid4

import pytest

from app.core.database import db
from app.modules.agents.automations import (
    AutomationCreate,
    AutomationError,
    AutomationExecutionBinding,
    AutomationRepository,
    AutomationUpdate,
    automation_configuration,
)
from app.modules.intelligence.contracts import fingerprint
from tests.integration.test_intelligence_metadata import intelligence_db as intelligence_db

pytestmark = pytest.mark.engine


async def test_real_automation_identity_recovers_and_compensation_checks_configuration(
    intelligence_db,
):
    scope = intelligence_db.model_copy(update={"session_id": None})
    repository = AutomationRepository()
    agent_id = "automation-test-" + uuid4().hex
    body = AutomationCreate(
        title="Revenue report",
        prompt="Report governed weekly revenue.",
        schedule_kind="cron",
        schedule_expr="0 8 * * 1",
        timezone="Asia/Jakarta",
    )
    binding = AutomationExecutionBinding(
        action_id=fingerprint([agent_id, "action"]),
        scope=scope,
        semantic={"view_id": "governed-sales", "version": 1, "fingerprint": "definition"},
    )
    args = {
        "agent_id": agent_id,
        "owner_name": scope.principal,
        "role_name": scope.active_role,
        "body": body,
        "creation_identity": binding.action_id,
        "execution_binding": binding,
    }
    identifier = None
    try:
        created = await repository.create(**args)
        identifier = created["automation_id"]
        assert (await repository.create(**args))["automation_id"] == identifier
        assert len(await repository.list(agent_id=agent_id, owner_name=scope.principal)) == 1
        assert created["delivery"]["execution_binding"] == binding.model_dump(mode="json")
        assert await repository.get(identifier, owner_name=scope.principal + "-foreign") is None
        with pytest.raises(AutomationError, match="inputs changed"):
            await repository.create(
                **(args | {"body": body.model_copy(update={"title": "Changed"})})
            )
        original_digest = fingerprint(automation_configuration(created))
        changed = await repository.update(
            created, AutomationUpdate(prompt="A different governed report.")
        )
        with pytest.raises(AutomationError, match="configuration changed"):
            await repository.update(
                created,
                AutomationUpdate(enabled=False),
                expected_configuration_digest=original_digest,
            )
        disabled = await repository.update(
            changed,
            AutomationUpdate(enabled=False),
            expected_configuration_digest=fingerprint(automation_configuration(changed)),
        )
        assert disabled["enabled"] is False
        assert disabled["delivery"] == created["delivery"]
    finally:
        if identifier:
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_AUTOMATIONS "
                "WHERE automation_id=%s AND owner_name=%s",
                [identifier, scope.principal],
            )
