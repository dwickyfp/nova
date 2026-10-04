"""Canonical Context references use each source's existing access contract."""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from pydantic import Field

from app.modules.intelligence.contracts import Contract, Scope, SemanticRef, fingerprint


class ContextSourceRef(Contract):
    kind: Literal[
        "mission",
        "action",
        "deliverable",
        "agent_release",
        "dashboard",
        "artifact",
        "document",
        "decision",
        "outcome",
        "investigation",
        "news",
        "agent",
        "skill",
        "tool",
        "policy",
    ]
    id: str = Field(min_length=1, max_length=128)
    revision: int | None = Field(default=None, ge=1)
    parent_id: str | None = Field(default=None, max_length=128)
    agent_id: str | None = Field(default=None, max_length=64)
    run_id: str | None = Field(default=None, max_length=64)
    fingerprint: str | None = Field(default=None, max_length=128)


async def read_context_source(ref: ContextSourceRef, user: dict, budget) -> dict:
    from app.modules.intelligence.engine import intelligence_service

    budget.consume("items")
    scope = Scope.from_user(user)
    semantic = None
    authority, source_kind = "recorded_lifecycle_evidence", "lifecycle_evidence"
    if ref.kind in {"action", "decision", "outcome", "investigation", "news"}:
        if ref.revision is None:
            raise HTTPException(422, "Pin the canonical source revision")
        kind = {"news": "news"}.get(ref.kind, f"{ref.kind}s")
        if ref.parent_id and ref.kind != "news":
            from app.modules.agents.mission import mission_service
            from app.modules.agents.mission_schema import ObjectRef
            from app.modules.intelligence.engine import MODELS

            value = await mission_service.canonical_read(
                ref.parent_id,
                ObjectRef(kind=ref.kind, id=ref.id, revision=ref.revision),
                user,
                budget=budget,
            )
            source = MODELS[kind].model_validate(value)
        else:
            source = await intelligence_service.get(
                kind,
                ref.id,
                user,
                budget=budget,
                revision=ref.revision,
            )
        semantic = getattr(source, "semantic", None)
        value = source.model_dump(mode="json")
        name = f"{ref.kind.capitalize()}: {getattr(source, 'status', 'recorded')}"
        digest = fingerprint(value)
    elif ref.kind in {"mission", "deliverable"}:
        from app.modules.agents.mission import mission_service

        mission_id = ref.id if ref.kind == "mission" else ref.parent_id
        if not mission_id:
            raise HTTPException(422, "A deliverable requires its Mission reference")
        mission = await mission_service.get(mission_id, user, project=False)
        if ref.kind == "mission":
            if ref.revision != mission.revision:
                raise HTTPException(409, "Mission context changed; select its current revision")
            source = mission
        else:
            source = next(
                (
                    item
                    for item in await mission_service.deliverables(mission_id, user, budget=budget)
                    if item.deliverable_id == ref.id
                ),
                None,
            )
            if source is None:
                raise HTTPException(404, "Deliverable unavailable")
            if ref.revision != source.mission_revision:
                raise HTTPException(409, "Deliverable Mission revision changed")
        value = source.model_dump(mode="json")
        if ref.kind == "deliverable":
            for obj in source.object_refs:
                budget.consume("items")
                await mission_service.canonical_read(mission_id, obj, user, budget=budget)
        name = "Mission" if ref.kind == "mission" else f"Deliverable: {source.kind}"
        digest = fingerprint(value)
    elif ref.kind == "agent":
        from app.modules.agents.router import _require_agent

        source = await _require_agent(ref.id, user)
        if source.get("owner_name") != scope.principal:
            raise HTTPException(404, "Agent context unavailable")
        value = {"agent_id": ref.id, "owner_name": scope.principal}
        name, digest = "Agent", fingerprint(value)
        authority, source_kind = "authorized_agent_identity", "studio_reference"
    elif ref.kind in {"skill", "tool", "policy"}:
        if not ref.parent_id or not ref.agent_id:
            raise HTTPException(422, "Pin the source release for this dependency")
        release = await read_context_source(
            ContextSourceRef(
                kind="agent_release",
                id=ref.parent_id,
                agent_id=ref.agent_id,
            ),
            user,
            budget,
        )
        dependencies = release["value"]["dependencies"]
        if ref.kind == "policy":
            if ref.id != "agent_policy" or not dependencies.get("policy_fingerprint"):
                raise HTTPException(404, "Release policy reference unavailable")
            value = {"fingerprint": dependencies["policy_fingerprint"]}
            name = "Agent policy reference"
        else:
            value = next(
                (
                    item
                    for item in dependencies.get(f"{ref.kind}s", [])
                    if item.get("name") == ref.id
                ),
                None,
            )
            if value is None:
                raise HTTPException(404, "Release dependency unavailable")
            name = f"{ref.kind.capitalize()}: {ref.id}"
        digest = fingerprint(value)
        authority, source_kind = "recorded_agent_release", "agent_release"
    elif ref.kind == "agent_release":
        from app.modules.agents.releases import get_manifest
        from app.modules.agents.resources import validate_resources
        from app.modules.agents.router import _require_agent

        if not ref.agent_id:
            raise HTTPException(422, "A release requires its agent reference")
        if ref.parent_id:
            from app.modules.agents.mission import mission_service

            mission = await mission_service.get(ref.parent_id, user, project=False)
            pin = next(
                (pin for pin in mission.release_pins if (
                    pin.manifest_id, pin.agent_id, pin.run_id, pin.fingerprint,
                ) == (ref.id, ref.agent_id, ref.run_id, ref.fingerprint)),
                None,
            )
            if pin is None:
                raise HTTPException(404, "Mission release reference unavailable")
            value = await mission_service.authorize_release_pin(pin, user, budget=budget)
        else:
            agent = await _require_agent(ref.agent_id, user)
            if agent.get("owner_name") != scope.principal:
                raise HTTPException(404, "Release context unavailable")
            value = await get_manifest(ref.agent_id, scope.principal, manifest_id=ref.id)
            if not value or value.get("id") != ref.id:
                raise HTTPException(404, "Release context unavailable")
            if value.get("fingerprint") != fingerprint(value.get("dependencies")):
                raise HTTPException(409, "Release manifest requires reconciliation")
            for pin in value["dependencies"].get("semantic_views", [])[:16]:
                await intelligence_service.authorize_semantic(
                    SemanticRef.model_validate(pin), user, budget=budget,
                )
            await validate_resources(dict(value["dependencies"].get("configuration") or {}), user)
        name, digest = "Agent release", value["fingerprint"]
        authority, source_kind = "recorded_agent_release", "agent_release"
    elif ref.kind in {"dashboard", "artifact"}:
        if ref.kind == "dashboard":
            from app.modules.agents.dashboard_repository import dashboard_repository

            source = await dashboard_repository.get(ref.id, owner_name=scope.principal)
            if source is None:
                raise HTTPException(404, "Dashboard unavailable")
            value = {
                "updated_at": str(source["updated_at"]),
                "layout": source["layout"].model_dump(mode="json"),
            }
        else:
            from app.modules.agents.artifact_repository import artifact_repository

            source = await artifact_repository.get(ref.id, owner_name=scope.principal)
            if source is None:
                raise HTTPException(404, "Artifact unavailable")
            value = {
                key: source.get(key)
                for key in (
                    "updated_at",
                    "sql_text",
                    "chart_spec",
                    "database_name",
                    "schema_name",
                )
            }
        digest = fingerprint(value)
        if not ref.fingerprint:
            raise HTTPException(422, "Pin the mutable Studio source fingerprint")
        name = f"Studio {ref.kind} reference"
        authority, source_kind = "owner_scoped_reference", "studio_reference"
    else:
        from app.modules.agents.harness_repository import harness_repository
        from app.modules.agents.resource_delegation import resource_delegation

        if not ref.run_id or not ref.fingerprint:
            raise HTTPException(422, "Pin the document grant and content digest")
        run = await harness_repository.get(ref.run_id)
        if run is None:
            raise HTTPException(404, "Document participant unavailable")
        try:
            metadata = await resource_delegation.available(run, user)
            source = next((item for item in metadata if item.resource_id == ref.id), None)
            if source is None:
                raise HTTPException(404, "Document grant unavailable")
            await resource_delegation.load(run, user)
        except ValueError as exc:
            raise HTTPException(404, "Document grant unavailable") from exc
        value = source.model_dump(mode="json")
        digest, name = source.digest, "Granted document reference"
        authority, source_kind = "participant_granted_reference", "studio_reference"
    if ref.fingerprint and digest != ref.fingerprint:
        raise HTTPException(409, "Context source changed; review its current reference")
    return {
        "name": name,
        "semantic": semantic,
        "fingerprint": digest,
        "source_kind": source_kind,
        "authority": authority,
        "value": value,
    }


def context_reference_id(scope: Scope, ref: ContextSourceRef) -> str:
    return fingerprint([scope.model_dump(exclude={"session_id"}), ref.model_dump(mode="json")])


async def authorize_context_reference(node, user: dict, budget) -> dict:
    ref = ContextSourceRef(
        kind=node.kind,
        id=node.reference_id,
        revision=node.reference_revision,
        parent_id=node.reference_parent_id,
        agent_id=node.reference_agent_id,
        run_id=node.reference_run_id,
        fingerprint=node.reference_fingerprint,
    )
    if node.id != context_reference_id(node.scope, ref):
        raise HTTPException(404, "Canonical Context reference unavailable")
    return await read_context_source(ref, user, budget)
