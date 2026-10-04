"""Apply reviewed improvement patches through existing draft and Semantic proposal owners."""

from __future__ import annotations

from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException

from app.modules.agents import quality
from app.modules.agents.doctor import RESTORABLE_FIELDS, RemediationPatch
from app.modules.agents.instructions import InstructionCompilationError, compile_agent_instructions
from app.modules.agents.repository import agent_repository
from app.modules.agents.versions import (
    agent_versions,
    revision_id,
    validate_publication_dependencies,
)
from app.modules.intelligence.contracts import Scope, fingerprint


async def require_base(agent: dict, base_revision: str, user: dict) -> dict:
    current = await agent_repository.get_agent(agent["agent_id"], owner_name=user["username"])
    if current is None:
        raise HTTPException(404, "Agent not found")
    if revision_id(current) != base_revision:
        raise HTTPException(409, "The improvement base changed; diagnose the current release again")
    return current


async def draft_for_patch(
    agent: dict, patch: RemediationPatch, version_id: str, user: dict
) -> dict:
    from app.modules.agents.resources import validate_resources
    from app.modules.agents.router import _unavailable_mcp_tools, _unknown_tools
    from app.modules.agents.schemas import AgentUpdateRequest
    from app.modules.intelligence.semantic_views import semantic_view_service

    current = await require_base(agent, patch.base_revision, user)
    if patch.kind == "restore_configuration":
        if not patch.fields or set(patch.fields) - RESTORABLE_FIELDS:
            raise HTTPException(422, "Review supported configuration fields before applying")
        source = await agent_versions.get(
            agent["agent_id"], user["username"], patch.source_version_id
        )
        if (
            not source
            or fingerprint(source["configuration"]) != patch.source_configuration_fingerprint
        ):
            raise HTTPException(409, "The pinned remediation source is unavailable or changed")
        fields = {key: source["configuration"].get(key) for key in patch.fields}
    elif patch.kind == "append_instruction" and patch.instruction:
        response = current.get("instructions_response") or ""
        fields = {"instructions_response": "\n\n".join([response, patch.instruction]).strip()}
    else:
        raise HTTPException(422, "Review a concrete agent configuration patch")
    fields = AgentUpdateRequest.model_validate(fields).model_dump(exclude_unset=True)
    candidate = {**current, **fields}
    tools = candidate.get("default_tools") or []
    if _unknown_tools(tools) or await _unavailable_mcp_tools(tools):
        raise HTTPException(422, "A configured agent tool is unavailable")
    await validate_publication_dependencies(candidate, user["username"])
    await validate_resources(candidate, user)
    for view_id in candidate.get("semantic_view_ids") or []:
        if not await semantic_view_service.get_active_for_agent(
            view_id, user, agent_id=agent["agent_id"]
        ):
            raise HTTPException(404, "A configured Semantic View is unavailable")
    try:
        candidate["compiled_instructions"] = compile_agent_instructions(
            response=candidate.get("instructions_response") or "",
            orchestration=candidate.get("instructions_orchestration") or "",
            description=candidate.get("description") or "",
            response_style=candidate.get("response_style"),
        ).as_dict()
    except InstructionCompilationError as exc:
        raise HTTPException(422, "The reviewed instruction patch is invalid") from exc
    await require_base(agent, patch.base_revision, user)
    draft = await agent_versions.store(
        candidate, label="Reviewed improvement draft", version_id=version_id
    )
    await require_base(agent, patch.base_revision, user)
    return draft


async def semantic_for_patch(
    agent: dict, patch: RemediationPatch, operation_id: str, user: dict
) -> dict:
    from app.modules.intelligence import autopilot
    from app.modules.intelligence.semantic_views import semantic_view_service

    await require_base(agent, patch.base_revision, user)
    if patch.semantic is None or not patch.changes:
        raise HTTPException(422, "Review a pinned Semantic change before applying")
    body = autopilot.AutopilotProposal(
        operation_id=operation_id,
        agent_id=agent["agent_id"],
        base=patch.semantic,
        changes=patch.changes,
    )
    view = await semantic_view_service._owned(patch.semantic.view_id, user)
    if view["active_version"] != patch.semantic.version:
        raise HTTPException(409, "The Semantic proposal base changed; diagnose its current version")
    await semantic_view_service.get_version_for_agent(
        patch.semantic.view_id,
        patch.semantic.version,
        patch.semantic.fingerprint,
        user,
        agent_id=agent["agent_id"],
    )
    return await autopilot.propose(patch.semantic.view_id, body, user)


async def regression_cases(proposal: dict, selected: list[str], user: dict, lock) -> list[dict]:
    candidates = {item["id"]: item for item in proposal.get("regression_candidates", [])}
    if set(selected) - set(candidates):
        raise HTTPException(422, "Select regression cases from the reviewed proposal")
    created = []
    for identifier in selected:
        ref = candidates[identifier]
        runs = await quality.records("runs", proposal["agent_id"], user, ref["run_id"])
        source = next(
            (
                case
                for case in (runs[0].get("cases", []) if runs else [])
                if case["id"] == ref["case_id"] and case["revision"] == ref["case_revision"]
            ),
            None,
        )
        if source is None or fingerprint(source) != ref["case_fingerprint"]:
            raise HTTPException(409, "The frozen regression source is unavailable or changed")
        current = await quality.records("cases", proposal["agent_id"], user, ref["case_id"])
        if not current or current[0]["revision"] != ref["case_revision"]:
            raise HTTPException(
                409, "A regression case base changed; review the current case again"
            )
        new_id = fingerprint([proposal["application"]["operation_id"], identifier, "case"])
        found = await quality.records("cases", proposal["agent_id"], user, new_id)
        if not found:
            body = quality.CaseRequest.model_validate(
                {
                    key: value
                    for key, value in source.items()
                    if key in quality.CaseRequest.model_fields
                }
            )
            record = {
                **body.model_dump(exclude={"expected_revision"}),
                "source": "regression",
                "id": new_id,
                "agent_id": proposal["agent_id"],
                "source_case": ref,
                "application_operation_id": proposal["application"]["operation_id"],
            }
            found = [await quality.save_record("cases", record, user)]
        if not await lock.renew():
            raise HTTPException(409, "Quality metadata lease expired")
        created.append({"id": found[0]["id"], "revision": found[0]["revision"]})
    return created


async def review_and_apply(
    agent: dict, proposal_id: str, body: quality.ProposalReview, user: dict
) -> dict:
    scope = Scope.from_user(user)
    async with quality.metadata_lock(f"agent-quality:proposals:{proposal_id}") as lock:
        found = await quality.records("proposals", agent["agent_id"], user, proposal_id)
        if not found:
            raise HTTPException(404, "Improvement proposal not found")
        proposal = found[0]
        selected_patch = body.patch_id or next(
            (item["id"] for item in proposal.get("patches", [])), None
        )
        request = {
            "revision": body.expected_revision,
            "resolution": body.resolution,
            "patch_id": selected_patch,
            "regression_case_ids": sorted(body.regression_case_ids),
        }
        digest = fingerprint(request)
        previous = proposal.get("review_request_digest")
        if proposal["status"] != "proposed":
            if previous != digest:
                raise HTTPException(
                    409, "Improvement proposal has already been reviewed with different inputs"
                )
            if (
                body.resolution == "rejected"
                or (proposal.get("application") or {}).get("status") == "applied"
            ):
                return proposal
        else:
            if proposal["revision"] != body.expected_revision:
                raise HTTPException(409, "Improvement proposal changed; reload before reviewing")
            if body.resolution == "rejected":
                return await quality._save_record_locked(
                    "proposals",
                    {**proposal, "status": "rejected", "review_request_digest": digest},
                    user,
                    proposal["revision"],
                    lock,
                )
        if body.resolution != "accepted":
            raise HTTPException(409, "The improvement review inputs conflict")
        patch = next(
            (item for item in proposal.get("patches", []) if item["id"] == selected_patch), None
        )
        if patch is None:
            raise HTTPException(
                422, "Diagnose this release and review a concrete remediation patch"
            )
        patch = RemediationPatch.model_validate(patch)
        await require_base(agent, patch.base_revision, user)
        candidates = {item["id"] for item in proposal.get("regression_candidates", [])}
        if set(body.regression_case_ids) - candidates:
            raise HTTPException(422, "Select regression cases from the reviewed proposal")
        operation_id = fingerprint(
            [proposal_id, scope.model_dump(exclude={"session_id"}), digest, "application-v1"]
        )
        application = {
            "operation_id": operation_id,
            "status": "pending",
            "patch_id": patch.id,
            "kind": "semantic_proposal" if patch.kind == "semantic_changes" else "agent_draft",
            "base_revision": patch.base_revision,
        }
        if proposal["status"] == "proposed":
            proposal = await quality._save_record_locked(
                "proposals",
                {
                    **proposal,
                    "status": "accepted",
                    "review_request_digest": digest,
                    "review_inputs": request,
                    "application": application,
                },
                user,
                proposal["revision"],
                lock,
            )
        elif proposal.get("application") != application:
            raise HTTPException(409, "The pending application requires reconciliation")
        if not await lock.renew():
            raise HTTPException(409, "Quality metadata lease expired")
        case_refs = await regression_cases(proposal, body.regression_case_ids, user, lock)
        if patch.kind == "semantic_changes":
            target = await semantic_for_patch(agent, patch, operation_id, user)
            reference = {"proposal_id": target["proposal_id"], "view_id": patch.semantic.view_id}
        else:
            version_id = str(uuid5(NAMESPACE_URL, operation_id))
            target = await draft_for_patch(agent, patch, version_id, user)
            reference = {"version_id": target["version_id"]}
        if not await lock.renew():
            raise HTTPException(409, "Quality metadata lease expired")
        return await quality._save_record_locked(
            "proposals",
            {
                **deepcopy(proposal),
                "application": {
                    **application,
                    **reference,
                    "status": "applied",
                    "regression_cases": case_refs,
                },
            },
            user,
            proposal["revision"],
            lock,
        )
