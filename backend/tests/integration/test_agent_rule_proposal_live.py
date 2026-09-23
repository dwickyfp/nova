"""Opt-in StarRocks check for the reviewed business-rule store."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.database import db
from app.modules.agents.rule_proposals import (
    candidate_definition,
    rule_proposal_repository,
)
from app.modules.agents.semantic.ossie import parse_ossie


@pytest.mark.asyncio
async def test_rule_proposal_store_scopes_and_versions() -> None:
    if os.getenv("NOVA_RUN_LIVE_RULE_PROPOSAL") != "1":
        pytest.skip("Set NOVA_RUN_LIVE_RULE_PROPOSAL=1 for live StarRocks check")
    await db.init_system_pool()
    proposal_id = ""
    model_id = f"rule-test-{uuid4().hex}"
    owner = f"rule-test-{uuid4().hex}"
    try:
        await rule_proposal_repository.ensure_schema()
        definition = parse_ossie(
            Path("app/modules/agents/examples/nova_sales.ossie.yaml").read_text()
        ).as_dict()
        _, previous, before_fp, after_fp, _, _ = candidate_definition(
            definition, "total_revenue", "SUM(orders.total_amount) - 1"
        )
        created = await rule_proposal_repository.create(
            {
                "owner_name": owner,
                "agent_id": "test-agent",
                "role_name": "test-role",
                "memory_id": "test-memory",
                "semantic_model_id": model_id,
                "metric_name": "total_revenue",
                "prior_expression": previous,
                "proposed_expression": "SUM(orders.total_amount) - 1",
                "prior_fingerprint": before_fp,
                "proposed_fingerprint": after_fp,
            }
        )
        proposal_id = created["proposal_id"]
        assert (
            await rule_proposal_repository.get(proposal_id, owner_name=owner, role_name="test-role")
        )["status"] == "pending"
        assert (
            await rule_proposal_repository.get(
                proposal_id, owner_name=owner, role_name="other-role"
            )
            is None
        )
        assert (
            await rule_proposal_repository.get(
                proposal_id, owner_name="other-owner", role_name="test-role"
            )
            is None
        )
        assert (
            len(
                await rule_proposal_repository.list(
                    owner_name=owner, role_name="test-role", semantic_model_id=model_id
                )
            )
            == 1
        )
        await rule_proposal_repository.mark_previewed(
            proposal_id, owner_name=owner, role_name="test-role"
        )
        assert (
            await rule_proposal_repository.get(proposal_id, owner_name=owner, role_name="test-role")
        )["previewed_at"] is not None
        version_id = await rule_proposal_repository.save_version(
            model_id=model_id,
            owner_name=owner,
            proposal_id=proposal_id,
            fingerprint=before_fp,
            definition=definition,
        )
        version = await db.execute_system(
            "SELECT model_fingerprint FROM NOVA_SYSTEM.CONFIG_SEMANTIC_MODEL_VERSIONS "
            "WHERE version_id = %s AND owner_name = %s",
            [version_id, owner],
        )
        assert version["rows"] == [[before_fp]]
        await rule_proposal_repository.set_status(
            proposal_id, owner_name=owner, role_name="test-role", status="approved"
        )
        assert (
            await rule_proposal_repository.get(proposal_id, owner_name=owner, role_name="test-role")
        )["status"] == "approved"
    finally:
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_SEMANTIC_MODEL_VERSIONS "
            "WHERE semantic_model_id = %s AND owner_name = %s",
            [model_id, owner],
        )
        if proposal_id:
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_RULE_PROPOSALS "
                "WHERE proposal_id = %s AND owner_name = %s",
                [proposal_id, owner],
            )
        await db.close_system_pool()
