"""Reviewable semantic discovery using the existing proposal and publication owners."""

from __future__ import annotations

import copy
import json
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, HTTPException
from pydantic import Field

from app.core.config import settings
from app.modules.agents.rule_proposals import rule_proposal_repository
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.learning_sources import LearningRequest, collect_observations
from app.modules.agents.semantic.runtime import validate_semantic_model_ir
from app.modules.agents.semantic.serialize import to_ossie_document
from app.modules.assistant.security import session_security
from app.modules.intelligence.contracts import Contract, Scope, SemanticRef, fingerprint
from app.modules.intelligence.engine_repository import metadata_lock
from app.modules.intelligence.responses import IntelligenceResponse, IntelligenceRoute
from app.modules.intelligence.semantic_views import (
    _IDENT,
    CurrentUser,
    SemanticViewVersionCreate,
    semantic_view_service,
)

router = APIRouter(default_response_class=IntelligenceResponse, route_class=IntelligenceRoute)


class SemanticChange(Contract):
    kind: Literal["dataset", "relationship", "dimension", "metric", "synonyms", "filter"]
    name: str = Field(min_length=1, max_length=128)
    dataset: str | None = Field(default=None, max_length=128)
    definition: dict = Field(default_factory=dict)
    synonyms: list[str] = Field(default_factory=list, max_length=32)
    target: Literal["metrics", "datasets", "named_filters"] = "metrics"


class AutopilotProposal(Contract):
    operation_id: str = Field(min_length=8, max_length=128)
    agent_id: str = Field(min_length=1, max_length=64)
    base: SemanticRef
    changes: list[SemanticChange] = Field(min_length=1, max_length=30)
    usage_digest: str | None = Field(default=None, min_length=64, max_length=64)
    learning: LearningRequest | None = None
    learning_digest: str | None = Field(default=None, min_length=64, max_length=64)


class AutopilotDiscovery(Contract):
    sources: list[str] = Field(min_length=1, max_length=8)
    semantic: SemanticRef | None = None


class AutopilotReview(Contract):
    proposed_fingerprint: str = Field(min_length=1, max_length=128)
    acknowledge_regressions: bool = False


def apply_changes(definition: dict, changes: list[SemanticChange]) -> dict:
    candidate = copy.deepcopy(definition)
    seen = set()
    for change in changes:
        identity = (change.kind, change.dataset, change.target, change.name)
        if identity in seen:
            raise ValueError("Review one change per semantic object in a proposal")
        seen.add(identity)
        sections = {
            "dataset": "datasets",
            "relationship": "relationships",
            "metric": "metrics",
            "filter": "named_filters",
        }
        if change.kind == "dimension":
            matches = [item for item in candidate["datasets"] if item["name"] == change.dataset]
            if len(matches) != 1:
                raise ValueError("Choose exactly one existing dataset for the dimension")
            collection = matches[0].setdefault("fields", [])
        else:
            collection = candidate.setdefault(
                change.target if change.kind == "synonyms" else sections[change.kind], []
            )
        matches = [item for item in collection if item["name"] == change.name]
        if len(matches) > 1:
            raise ValueError("A semantic name is ambiguous")
        if change.kind == "synonyms":
            if not matches:
                raise ValueError("The synonym target does not exist")
            matches[0]["synonyms"] = change.synonyms
        else:
            replacement = {**change.definition, "name": change.name}
            if matches:
                collection[collection.index(matches[0])] = replacement
            else:
                collection.append(replacement)
    parsed, ir = semantic_view_service._parse(
        json.dumps(to_ossie_document(candidate)), candidate["name"]
    )
    validation = validate_semantic_model_ir(ir)
    if not validation.valid:
        raise ValueError("; ".join(validation.errors))
    if ir.fingerprint == SemanticModelIR.from_ossie(definition).fingerprint:
        raise ValueError("The proposed changes do not change the definition")
    return parsed


@router.post("/autopilot/discover")
async def discover(body: AutopilotDiscovery, user: CurrentUser):
    from app.modules.query.service import query_service

    session_security(user)
    candidates, citations, discovered = [], [], []
    for source in dict.fromkeys(body.sources):
        if len(source.split(".")) not in (2, 3) or not all(
            _IDENT.fullmatch(part) for part in source.split(".")
        ):
            raise HTTPException(status_code=422, detail="Use a qualified source name")
        if not await semantic_view_service._source_access({"datasets": [{"source": source}]}, user):
            raise HTTPException(status_code=404, detail="Source unavailable")
        quoted = ".".join(f"`{part}`" for part in source.split("."))
        result = await query_service.execute(
            sql=f"DESCRIBE {quoted}",
            username=user["username"],
            encrypted_password=user["encrypted_password"],
            role=user["active_role"],
            security_context_version=session_security(user).security_context_version,
            session_id=user.get("session_id"),
            database=source.split(".")[-2],
            max_rows=501,
        )
        if result.error or len(result.rows) > 500:
            raise HTTPException(
                status_code=422,
                detail="Source metadata is unavailable or exceeds the discovery bound",
            )
        fields = []
        for row in result.rows:
            name, datatype = str(row[0]), str(row[1]).upper()
            is_time = datatype.startswith(("DATE", "TIMESTAMP"))
            numeric = datatype.startswith(
                ("INT", "BIGINT", "SMALLINT", "TINYINT", "LARGEINT", "DECIMAL", "FLOAT", "DOUBLE")
            )
            fields.append(
                {
                    "name": name,
                    "expression": name,
                    "datatype": str(row[1]),
                    "dimension": {"is_time": is_time},
                    "ai_context": {"source_datatype": datatype, "numeric": numeric},
                }
            )
        name = source.split(".")[-1]
        keys = [str(row[0]) for row in result.rows if len(row) > 3 and str(row[3]).upper() == "YES"]
        discovered.append({"name": name, "source": source, "fields": fields, "primary_key": keys})
        candidates.append(
            {
                "kind": "dataset",
                "name": name,
                "state": "INFERRED",
                "definition": {"source": source, "fields": fields, "primary_key": keys},
                "review_required": ["grain", "time_dimensions", "authority", "relationships"],
            }
        )
        for field in fields:
            candidates.append(
                {
                    "kind": "dimension",
                    "name": field["name"],
                    "dataset": name,
                    "definition": field,
                    "state": "INFERRED",
                }
            )
        citations.append(
            {
                "source": source,
                "method": "authorized-schema-metadata-v1",
                "digest": fingerprint(fields),
                "query_id": getattr(result, "query_id", None),
            }
        )
    for source in discovered:
        columns = {field["name"] for field in source["fields"]}
        for target in discovered:
            keys = target["primary_key"]
            if source is target or not keys or not set(keys) <= columns:
                continue
            candidates.append(
                {
                    "kind": "relationship",
                    "name": f"{source['name']}_{target['name']}",
                    "state": "HYPOTHESIS",
                    "definition": {
                        "from": source["name"],
                        "to": target["name"],
                        "from_columns": keys,
                        "to_columns": keys,
                        "cardinality": "many_to_one",
                    },
                    "review_required": ["key_semantics", "referential_integrity", "cardinality"],
                }
            )
    if body.semantic:
        from app.modules.intelligence.engine import intelligence_service

        published = await intelligence_service.authorize_semantic(body.semantic, user, active=True)
        definition = published["definition"]
        for kind, section in (("metric", "metrics"), ("filter", "named_filters")):
            for item in definition.get(section) or []:
                if item.get("visibility", "public") != "public":
                    continue
                candidates.append(
                    {
                        "kind": kind,
                        "name": item["name"],
                        "state": "VERIFIED",
                        "definition": item,
                        "semantic": body.semantic.model_dump(),
                        "review_required": ["compatibility_with_discovered_sources"],
                    }
                )
                if item.get("synonyms"):
                    candidates.append(
                        {
                            "kind": "synonyms",
                            "name": item["name"],
                            "target": section,
                            "synonyms": item["synonyms"],
                            "state": "VERIFIED",
                            "semantic": body.semantic.model_dump(),
                        }
                    )
        citations.append(
            {
                "source": body.semantic.view_id,
                "method": "published-definition-v1",
                "digest": published["fingerprint"],
                "version": body.semantic.version,
            }
        )
    if len(candidates) > 1000:
        raise HTTPException(status_code=422, detail="Narrow discovery to at most 1000 candidates")
    return {
        "candidates": candidates,
        "evidence": citations,
        "message": (
            "Review grains and business meaning before publication. "
            "Value profiling requires a governed query."
        ),
    }


@router.get("/{view_id}/autopilot/usage")
async def usage_observations(view_id: str, agent_id: str, user: CurrentUser):
    if not getattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False):
        raise HTTPException(status_code=404, detail="Usage learning is unavailable")
    from app.modules.agents.router import _require_agent
    from app.modules.agents.semantic.access import bound_view_ids
    from app.modules.agents.semantic.usage_learning import load_scoped_usage

    agent = await _require_agent(agent_id, user)
    if view_id not in bound_view_ids(agent):
        raise HTTPException(status_code=404, detail="Semantic View unavailable")
    view = await semantic_view_service._owned(view_id, user)
    if not view.get("active_version"):
        raise HTTPException(status_code=409, detail="Publish a Semantic View before usage learning")
    _, base = await semantic_view_service._readable_version(view_id, view["active_version"], user)
    return await load_scoped_usage(
        Scope.from_user(user),
        SemanticRef(view_id=view_id, version=base["version"], fingerprint=base["fingerprint"]),
        base["definition"],
    )


@router.post("/{view_id}/autopilot/usage/context")
async def project_usage(view_id: str, body: SemanticRef, user: CurrentUser):
    from app.modules.intelligence.context_graph import project_usage_context

    if body.view_id != view_id:
        raise HTTPException(status_code=422, detail="Use the same Semantic View in this request")
    await semantic_view_service._owned(view_id, user)
    return await project_usage_context(body, user)


@router.post("/{view_id}/autopilot/observations")
async def learning_observations(view_id: str, body: LearningRequest, user: CurrentUser):
    if not getattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False):
        raise HTTPException(404, "Governed learning is unavailable")
    if body.semantic.view_id != view_id:
        raise HTTPException(422, "Use the selected Semantic View")
    return await collect_observations(body, user)


@router.post("/{view_id}/autopilot/proposals", status_code=201)
async def propose(view_id: str, body: AutopilotProposal, user: CurrentUser):
    from app.modules.agents.router import _require_agent
    from app.modules.agents.semantic.access import bound_view_ids

    agent = await _require_agent(body.agent_id, user)
    if view_id != body.base.view_id or view_id not in bound_view_ids(agent):
        raise HTTPException(status_code=422, detail="Use a Semantic View bound to this agent")
    await semantic_view_service._owned(view_id, user)
    view, base = await semantic_view_service._readable_version(view_id, body.base.version, user)
    if view["active_version"] != body.base.version or base["fingerprint"] != body.base.fingerprint:
        raise HTTPException(
            status_code=409, detail="The Semantic View changed; rediscover its current definition"
        )
    try:
        candidate = apply_changes(base["definition"], body.changes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not await semantic_view_service._source_access(candidate, user):
        raise HTTPException(status_code=404, detail="A proposed source is unavailable")
    scope = Scope.from_user(user)
    role = scope.active_role
    usage = None
    learning = None
    if (body.learning is None) != (body.learning_digest is None):
        raise HTTPException(422, "Review the exact learning observations before proposing")
    if body.learning:
        if not getattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False):
            raise HTTPException(404, "Governed learning is unavailable")
        if body.learning.agent_id != body.agent_id or body.learning.semantic != body.base:
            raise HTTPException(422, "Learning must use the proposal agent and semantic base")
        learning = await collect_observations(body.learning, user)
        if learning["digest"] != body.learning_digest:
            raise HTTPException(409, "Learning evidence changed; review its current observations")
        if not learning["observations"]:
            raise HTTPException(422, "No authorized learning observations support this proposal")
    if body.usage_digest:
        if not getattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False):
            raise HTTPException(status_code=404, detail="Usage learning is unavailable")
        from app.modules.agents.semantic.usage_learning import load_scoped_usage

        usage = await load_scoped_usage(scope, body.base, base["definition"])
        if usage["digest"] != body.usage_digest:
            raise HTTPException(
                status_code=409, detail="Usage changed; review the current observations"
            )
        if not usage["patterns"]:
            raise HTTPException(status_code=422, detail="No governed usage supports this proposal")
    request = body.model_dump(
        mode="json", exclude=(
            {"operation_id", "usage_digest", "learning", "learning_digest"}
            if usage or learning else {"usage_digest", "learning", "learning_digest"}
        ),
    )
    request_digest = fingerprint(request)
    proposal_id = str(
        uuid5(NAMESPACE_URL, fingerprint([
            user["username"], role,
            [scope.security_context_version, request_digest]
            if usage or learning else body.operation_id,
        ]))
    )
    async with metadata_lock(f"proposal:{proposal_id}"):
        existing = await rule_proposal_repository.get(
            proposal_id, owner_name=user["username"], role_name=role
        )
        if existing:
            if (existing.get("details") or {}).get("request_digest") != request_digest:
                raise HTTPException(status_code=409, detail="Operation inputs changed")
            return existing
        result = await rule_proposal_repository.create(
            {
                "proposal_id": proposal_id,
                "owner_name": user["username"],
                "role_name": role,
                "agent_id": body.agent_id,
                "memory_id": "",
                "semantic_model_id": view_id,
                "metric_name": "Semantic changes",
                "prior_expression": "",
                "proposed_expression": "",
                "prior_fingerprint": base["fingerprint"],
                "proposed_fingerprint": SemanticModelIR.from_ossie(candidate).fingerprint,
                "proposal_kind": "autopilot",
                "details": {
                    "request_digest": request_digest,
                    "base": body.base.model_dump(),
                    "changes": [item.model_dump() for item in body.changes],
                    "candidate_definition": candidate,
                    **({"usage_evidence": usage} if usage else {}),
                    **({
                        "learning_evidence": learning,
                        "learning_request": body.learning.model_dump(mode="json"),
                    } if learning else {}),
                },
            }
        )
    await semantic_view_service._audit("PROPOSE_AUTOPILOT", proposal_id, user)
    return result


async def _proposal(view_id: str, proposal_id: str, user: dict) -> tuple[dict, dict]:
    await semantic_view_service._owned(view_id, user)
    row = await rule_proposal_repository.get(
        proposal_id, owner_name=user["username"], role_name=session_security(user).active_role
    )
    if not row or row["semantic_model_id"] != view_id or row.get("proposal_kind") != "autopilot":
        raise HTTPException(status_code=404, detail="Proposal unavailable")
    usage = row["details"].get("usage_evidence")
    if usage and usage.get("scope") != Scope.from_user(user).model_dump(exclude={"session_id"}):
        raise HTTPException(status_code=404, detail="Proposal unavailable in this security context")
    learning = row["details"].get("learning_evidence")
    if learning:
        if learning.get("scope") != Scope.from_user(user).model_dump(exclude={"session_id"}):
            raise HTTPException(404, "Proposal unavailable in this security context")
        request = LearningRequest.model_validate(row["details"]["learning_request"])
        current = await collect_observations(request, user)
        if not current["observations"] or current["digest"] != learning["digest"]:
            raise HTTPException(409, "Proposal learning evidence changed; review it again")
    base = row["details"]["base"]
    await semantic_view_service._readable_version(view_id, base["version"], user)
    if not await semantic_view_service._source_access(row["details"]["candidate_definition"], user):
        raise HTTPException(status_code=404, detail="Proposed source unavailable")
    view = await semantic_view_service._get(view_id)
    active = await semantic_view_service._version(view_id, view["active_version"])
    if active["fingerprint"] not in {row["prior_fingerprint"], row["proposed_fingerprint"]}:
        raise HTTPException(
            status_code=409, detail="Semantic version changed; create a new proposal"
        )
    return row, active


@router.post("/{view_id}/autopilot/proposals/{proposal_id}/preview")
async def preview(view_id: str, proposal_id: str, user: CurrentUser):
    row, active = await _proposal(view_id, proposal_id, user)
    if row["status"] != "pending" or active["fingerprint"] != row["prior_fingerprint"]:
        raise HTTPException(status_code=409, detail="This proposal is no longer pending")
    async with metadata_lock(f"semantic-draft:{view_id}"):
        described = await semantic_view_service.describe(view_id, user)
        versions = described.get("versions") or []
        latest = max(versions, key=lambda version: version["version"])
        if latest["version"] > active["version"]:
            if latest["fingerprint"] != row["proposed_fingerprint"]:
                raise HTTPException(
                    status_code=409, detail="Review the existing draft before this proposal"
                )
            number = latest["version"]
        else:
            draft = await semantic_view_service.add_version(
                view_id,
                SemanticViewVersionCreate(
                    definition=json.dumps(to_ossie_document(row["details"]["candidate_definition"]))
                ),
                user,
            )
            number = draft["version"]
    report = await semantic_view_service.validate(view_id, number, user)
    if report["valid"]:
        await rule_proposal_repository.mark_previewed(
            proposal_id, owner_name=user["username"], role_name=session_security(user).active_role
        )
    return {
        "proposal": row,
        "version": number,
        "validation": report,
        "prior_definition": active["definition"],
        "proposed_definition": row["details"]["candidate_definition"],
    }


@router.post("/{view_id}/autopilot/proposals/{proposal_id}/approve")
async def approve(view_id: str, proposal_id: str, body: AutopilotReview, user: CurrentUser):
    row, active = await _proposal(view_id, proposal_id, user)
    if (
        row["proposed_fingerprint"] != body.proposed_fingerprint
        or row["previewed_at"] is None
        or row["status"] == "rejected"
    ):
        raise HTTPException(
            status_code=409, detail="Preview and review the exact candidate before approval"
        )
    if active["fingerprint"] != row["proposed_fingerprint"]:
        described = await semantic_view_service.describe(view_id, user)
        latest = max(described["versions"], key=lambda version: version["version"])
        if latest["fingerprint"] != body.proposed_fingerprint:
            raise HTTPException(status_code=409, detail="The draft changed; preview it again")
        await semantic_view_service.publish(
            view_id, latest["version"], user, acknowledge_regressions=body.acknowledge_regressions
        )
    await rule_proposal_repository.set_status(
        proposal_id,
        owner_name=user["username"],
        role_name=session_security(user).active_role,
        status="approved",
    )
    return await rule_proposal_repository.get(
        proposal_id, owner_name=user["username"], role_name=session_security(user).active_role
    )
