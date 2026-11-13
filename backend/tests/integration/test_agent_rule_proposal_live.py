"""Rule proposals through the runtime facade and real PG transaction boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.metadata_actor import acting_as
from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext
from app.modules.agents.rule_proposals import candidate_definition, rule_proposal_repository
from app.modules.agents.semantic.ossie import parse_ossie
from tests.integration.test_metadata_agents import agents, runtime_registry  # noqa: F401
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg


async def test_rule_proposal_store_scopes_and_decision_race(runtime_registry):  # noqa: F811
    definition = parse_ossie(
        Path("app/modules/agents/examples/nova_sales.ossie.yaml").read_text()
    ).as_dict()
    _, previous, before_fp, after_fp, _, _ = candidate_definition(
        definition, "total_revenue", "SUM(orders.total_amount) - 1",
    )
    owner, role = "agent-owner", "ANALYST"
    actor = SecurityContext.from_session(session(owner, active=role))
    repo = rule_proposal_repository
    proposal_id = str(uuid4())
    with acting_as(actor):
        created = await repo.create({
            "proposal_id": proposal_id, "owner_name": owner, "agent_id": "test-agent",
            "role_name": role, "memory_id": "test-memory", "semantic_model_id": "view-1",
            "metric_name": "total_revenue", "prior_expression": previous,
            "proposed_expression": "SUM(orders.total_amount) - 1",
            "prior_fingerprint": before_fp, "proposed_fingerprint": after_fp,
            "details": {"source": "fixture"},
        })
        assert created["proposal_id"] == proposal_id and created["status"] == "pending"
        assert created["details"] == {"source": "fixture"}
        assert "metadata_revision" not in created and "provenance" not in created
        assert (
            len(await repo.list(owner_name=owner, role_name=role, semantic_model_id="view-1")) == 1
        )
        for foreign_owner, foreign_role in (("other", role), (owner, "VIEWER")):
            with pytest.raises(MetadataPermissionDenied):
                await repo.get(proposal_id, owner_name=foreign_owner, role_name=foreign_role)
        await repo.mark_previewed(proposal_id, owner_name=owner, role_name=role)
        previewed = await repo.get(proposal_id, owner_name=owner, role_name=role)
        assert previewed["previewed_at"] is not None
        results = await asyncio.gather(*(
            repo.set_status(proposal_id, owner_name=owner, role_name=role, status=status)
            for status in ("approved", "rejected")
        ), return_exceptions=True)
        assert sum(item is None for item in results) == 1
        assert sum(isinstance(item, MetadataConflict) for item in results) == 1
        reviewed = await repo.get(proposal_id, owner_name=owner, role_name=role)
        assert reviewed["reviewed_by"] == owner
        assert reviewed["status"] in {"approved", "rejected"}
        with pytest.raises(MetadataConflict):
            await repo.mark_previewed(proposal_id, owner_name=owner, role_name=role)
    for name, active in (("other", role), (owner, "VIEWER")):
        with acting_as(SecurityContext.from_session(session(name, active=active))):
            assert await repo.get(proposal_id, owner_name=name, role_name=active) is None
            assert (
                await repo.list(owner_name=name, role_name=active, semantic_model_id="view-1") == []
            )
            with pytest.raises(MetadataConflict):
                await repo.set_status(proposal_id, owner_name=name, role_name=active,
                                      status="approved")
