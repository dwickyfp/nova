"""Opt-in version API roundtrip with real StarRocks persistence and audit writes."""

from __future__ import annotations

import json
import os
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.core.database import db
from app.modules.agents.repository import agent_repository
from app.modules.agents.router import get_current_user, router
from app.modules.agents.schemas import AgentCreateRequest


@pytest.mark.asyncio
async def test_save_publish_restore_with_real_audit() -> None:
    if os.getenv("NOVA_RUN_LIVE_AGENT_VERSIONS") != "1":
        pytest.skip("Set NOVA_RUN_LIVE_AGENT_VERSIONS=1 for live StarRocks check")

    owner = f"version-test-{uuid4().hex}"
    agent_id = None
    app = FastAPI()
    app.include_router(router, prefix="/agents")
    app.dependency_overrides[get_current_user] = lambda: {"username": owner}
    await db.init_system_pool()
    try:
        agent = await agent_repository.create_agent(
            owner_name=owner,
            fields=AgentCreateRequest(name="Version audit regression").model_dump(),
        )
        agent_id = agent["agent_id"]
        base_revision = agent["config_revision"]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            saved = await client.post(
                f"/agents/{agent_id}/versions",
                json={
                    "configuration": {"description": "Draft change"},
                    "expected_revision": base_revision,
                },
            )
            assert saved.status_code == 201, saved.text
            draft_id = saved.json()["version_id"]
            active = await agent_repository.get_agent(agent_id, owner_name=owner)
            assert active["description"] == ""
            assert active["config_revision"] == base_revision

            published = await client.post(
                f"/agents/{agent_id}/versions/{draft_id}/publish",
                json={"expected_revision": base_revision},
            )
            assert published.status_code == 200, published.text
            new_revision = published.json()["config_revision"]
            assert published.json()["description"] == "Draft change"

            restored = await client.post(
                f"/agents/{agent_id}/versions/{base_revision}/publish",
                json={"expected_revision": new_revision},
            )
            assert restored.status_code == 200, restored.text
            assert restored.json()["description"] == ""
            restored_revision = restored.json()["config_revision"]
            assert len({base_revision, new_revision, restored_revision}) == 3

            history = await client.get(f"/agents/{agent_id}/versions")
            assert history.status_code == 200, history.text
            assert len(history.json()["versions"]) == 4
            assert history.json()["active_version_id"] == restored_revision

        audit = await db.execute_system(
            "SELECT action,sql_text,decision FROM NOVA_SYSTEM.AUDIT_LOG "
            "WHERE user_name=%s AND object_name=%s",
            [owner, agent_id],
        )
        assert len(audit["rows"]) == 3
        details = [(action, json.loads(payload)) for action, payload, _ in audit["rows"]]
        assert ("SAVE_DRAFT", {"version_id": draft_id}) in details
        assert (
            "PUBLISH_VERSION", {"source_version": draft_id, "active_version": new_revision}
        ) in details
        assert (
            "PUBLISH_VERSION",
            {"source_version": base_revision, "active_version": restored_revision},
        ) in details
        assert all(decision is None for _, _, decision in audit["rows"])
    finally:
        try:
            if agent_id:
                await db.execute_system(
                    "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_VERSIONS "
                    "WHERE agent_id=%s AND owner_name=%s", [agent_id, owner],
                )
                await agent_repository.delete_agent(agent_id, owner_name=owner)
        finally:
            await db.close_system_pool()
