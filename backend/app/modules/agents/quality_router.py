"""Owned quality cases, evaluated releases, and reviewable improvement proposals."""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.common.audit import write_audit_log
from app.core.deps import get_current_user
from app.modules.agents import quality
from app.modules.agents.releases import capture_manifest, get_manifest
from app.modules.agents.versions import agent_versions

router = APIRouter()
CurrentUser = Annotated[dict, Depends(get_current_user)]


async def owned_agent(agent_id: str, user: dict) -> dict:
    from app.modules.agents.router import _require_agent

    agent = await _require_agent(agent_id, user)
    if agent.get("owner_name") != user["username"]:
        raise HTTPException(404, "Agent not found")
    return agent


async def audit(agent_id: str, user: dict, action: str) -> None:
    await write_audit_log(
        event_type="AGENT",
        action=action,
        object_type="AGENT",
        object_name=agent_id,
        user_name=user["username"],
        status="SUCCESS",
        session_id=user.get("session_id"),
        active_role=user.get("active_role"),
    )


@router.get("/{agent_id}/versions/{version_id}/manifest")
async def inspect_manifest(agent_id: str, version_id: str, user: CurrentUser):
    await owned_agent(agent_id, user)
    if not await agent_versions.get(agent_id, user["username"], version_id):
        raise HTTPException(404, "Agent version not found")
    manifest = await get_manifest(agent_id, user["username"], version_id=version_id)
    return {"manifest": manifest, "status": "pinned" if manifest else "unevaluated"}


@router.post("/{agent_id}/versions/{version_id}/manifest")
async def prepare_manifest(agent_id: str, version_id: str, user: CurrentUser):
    agent = await owned_agent(agent_id, user)
    version = await agent_versions.get(agent_id, user["username"], version_id)
    if not version:
        raise HTTPException(404, "Agent version not found")
    manifest = await capture_manifest({**agent, **version["configuration"]}, version_id, user)
    await audit(agent_id, user, "PREPARE_RELEASE")
    return manifest


@router.get("/{agent_id}/quality/cases")
async def list_cases(agent_id: str, user: CurrentUser):
    await owned_agent(agent_id, user)
    return {"items": await quality.records("cases", agent_id, user)}


@router.post("/{agent_id}/quality/cases", status_code=201)
async def create_case(agent_id: str, body: quality.CaseRequest, user: CurrentUser):
    await owned_agent(agent_id, user)
    record = await quality.save_record(
        "cases",
        {
            "id": str(uuid4()),
            "agent_id": agent_id,
            **body.model_dump(exclude={"expected_revision"}),
        },
        user,
    )
    await audit(agent_id, user, "CREATE_QUALITY_CASE")
    return record


@router.put("/{agent_id}/quality/cases/{case_id}")
async def update_case(agent_id: str, case_id: str, body: quality.CaseRequest, user: CurrentUser):
    await owned_agent(agent_id, user)
    record = await quality.save_record(
        "cases",
        {"id": case_id, "agent_id": agent_id, **body.model_dump(exclude={"expected_revision"})},
        user,
        body.expected_revision,
    )
    await audit(agent_id, user, "UPDATE_QUALITY_CASE")
    return record


@router.get("/{agent_id}/quality/runs")
async def list_runs(agent_id: str, user: CurrentUser):
    await owned_agent(agent_id, user)
    return {"items": await quality.records("runs", agent_id, user)}


@router.get("/{agent_id}/quality/runs/{run_id}")
async def get_run(agent_id: str, run_id: str, user: CurrentUser):
    await owned_agent(agent_id, user)
    found = await quality.records("runs", agent_id, user, run_id)
    if not found:
        raise HTTPException(404, "Quality run not found")
    return found[0]


@router.post("/{agent_id}/quality/runs")
async def run_cases(agent_id: str, body: quality.RunRequest, user: CurrentUser):
    agent = await owned_agent(agent_id, user)
    version = await agent_versions.get(agent_id, user["username"], body.version_id)
    if not version:
        raise HTTPException(404, "Agent version not found")
    manifest = await capture_manifest({**agent, **version["configuration"]}, body.version_id, user)
    cases = await quality.records("cases", agent_id, user)
    if body.case_ids:
        if set(body.case_ids) - {case["id"] for case in cases}:
            raise HTTPException(404, "A quality case is unavailable")
        cases = [case for case in cases if case["id"] in body.case_ids]
    result = await quality.evaluate(agent, manifest, cases, user, body.gates)
    await audit(agent_id, user, "EVALUATE_RELEASE")
    return result


@router.get("/{agent_id}/quality/comparison")
async def compare(agent_id: str, left: str, right: str, user: CurrentUser):
    left_run, right_run = await get_run(agent_id, left, user), await get_run(agent_id, right, user)

    def scores(run):
        return {
            (
                result["case_id"],
                result["case_revision"],
                score["scorer"],
                score["scorer_version"],
            ): score["status"]
            for result in run["results"]
            for score in result["scores"]
        }

    before, after = scores(left_run), scores(right_run)
    comparable = (
        left_run.get("case_fingerprint") == right_run.get("case_fingerprint")
        and left_run.get("gates", {}) == right_run.get("gates", {})
        and left_run.get("scorer_set_version") == right_run.get("scorer_set_version")
        and left_run["cases"] == right_run["cases"]
    )
    return {
        "left": left_run,
        "right": right_run,
        "comparable": comparable,
        "changes": [
            {
                "case_id": key[0],
                "case_revision": key[1],
                "scorer": key[2],
                "scorer_version": key[3],
                "before": before.get(key),
                "after": after.get(key),
                "regression": comparable and before.get(key) == "pass" and after.get(key) != "pass",
            }
            for key in sorted(set(before) | set(after))
            if before.get(key) != after.get(key)
        ],
    }


@router.get("/{agent_id}/quality/monitoring")
async def monitoring(agent_id: str, user: CurrentUser):
    await owned_agent(agent_id, user)
    found = await quality.records("monitoring", agent_id, user)
    return (
        found[0]
        if found
        else {
            "enabled": False,
            "sample_rate": 0.1,
            "max_traces": 20,
            "cadence_minutes": 60,
            "revision": 0,
        }
    )


@router.put("/{agent_id}/quality/monitoring")
async def set_monitoring(agent_id: str, body: quality.MonitoringRequest, user: CurrentUser):
    await owned_agent(agent_id, user)
    record = await quality.configure_monitoring(agent_id, body, user)
    await audit(agent_id, user, "CONFIGURE_QUALITY_MONITORING")
    return record


@router.get("/{agent_id}/quality/doctor")
async def doctor(agent_id: str, user: CurrentUser):
    from app.modules.agents.releases import load_runtime_manifest
    from app.modules.agents.service import agent_service

    agent = await owned_agent(agent_id, user)
    diagnosis = []
    try:
        manifest = await load_runtime_manifest(agent)
        if manifest:
            await agent_service.build_loop_inputs(agent)
        else:
            diagnosis.append(
                {
                    "category": "UNEVALUATED",
                    "hypothesis": False,
                    "detail": "This legacy agent has no evaluated release manifest.",
                }
            )
    except HTTPException as exc:
        diagnosis.append(
            {
                "category": "DEPENDENCY_DRIFT" if exc.status_code == 409 else "RUNTIME_UNAVAILABLE",
                "hypothesis": False,
                "detail": "The release dependency check failed.",
                "status_code": exc.status_code,
            }
        )
    except Exception:
        diagnosis.append(
            {
                "category": "RUNTIME_UNAVAILABLE",
                "hypothesis": False,
                "detail": "The release dependency check could not complete.",
            }
        )
    runs = await quality.records("runs", agent_id, user)
    for run in runs:
        for result in run.get("results", []):
            for score in result["scores"]:
                if score["status"] != "pass":
                    diagnosis.append(
                        {
                            "category": "EVIDENCE_UNAVAILABLE"
                            if score["status"] == "unavailable"
                            else score["failure_taxonomy"],
                            "hypothesis": False,
                            "observation": score["status"],
                            "scorer": score["scorer"],
                            "run_id": run["id"],
                            "case_id": result["case_id"],
                            "case_revision": result["case_revision"],
                            "detail": score["detail"],
                        }
                    )
    return {"agent_id": agent_id, "diagnoses": diagnosis[:100], "review_required": True}


@router.post("/{agent_id}/quality/runs/{run_id}/analyze")
async def analyze_run(agent_id: str, run_id: str, user: CurrentUser):
    run = await get_run(agent_id, run_id, user)
    if run["status"] == "running":
        raise HTTPException(409, "Wait for the evaluation to finish before analyzing")
    proposal = await quality.feedback_proposal(agent_id, run_id, user, run)
    await audit(agent_id, user, "ANALYZE_QUALITY_RUN")
    return proposal


@router.get("/{agent_id}/quality/proposals")
async def proposals(agent_id: str, user: CurrentUser):
    await owned_agent(agent_id, user)
    return {"items": await quality.records("proposals", agent_id, user)}


@router.post("/{agent_id}/quality/proposals/{proposal_id}/review")
async def review_proposal(
    agent_id: str,
    proposal_id: str,
    body: quality.ProposalReview,
    user: CurrentUser,
):
    await owned_agent(agent_id, user)
    found = await quality.records("proposals", agent_id, user, proposal_id)
    if not found:
        raise HTTPException(404, "Improvement proposal not found")
    if found[0]["status"] != "proposed":
        raise HTTPException(409, "Improvement proposal has already been reviewed")
    record = await quality.save_record(
        "proposals", {**found[0], "status": body.resolution}, user, body.expected_revision
    )
    await audit(agent_id, user, "REVIEW_IMPROVEMENT")
    return record
