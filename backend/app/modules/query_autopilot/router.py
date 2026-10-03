from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.deps import get_current_user
from app.core.role_gates import require_active_role
from app.modules.monitoring.router import READ_ROLES
from app.modules.query_autopilot.models import Candidate, Enrollment, Policy
from app.modules.query_autopilot.repository import repository
from app.modules.query_autopilot.service import AutopilotService, Conflict, public_record
from app.modules.query_autopilot.telemetry import collector

router = APIRouter()
service = AutopilotService()
Reader = Annotated[dict, Depends(require_active_role(*READ_ROLES))]


async def account_admin(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    if user.get("active_role") != "ACCOUNTADMIN":
        await service.audit(
            user,
            "AUTHORIZE",
            "administration",
            status="DENIED",
            reason="active_ACCOUNTADMIN_required",
        )
        raise HTTPException(403, "Activate ACCOUNTADMIN to manage Query Autopilot")
    return user


Administrator = Annotated[dict, Depends(account_admin)]
Collection = Literal[
    "families",
    "incidents",
    "opportunities",
    "experiments",
    "actions",
    "outcomes",
    "evidence",
    "baselines",
    "rollups",
    "enrollments",
    "jobs",
]


class Mutation(BaseModel):
    candidate_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")


@router.get("/overview")
async def overview(user: Reader):
    policy = await service.policy()
    counts = {}
    from app.core.database import db
    from app.modules.query_autopilot.schema import TABLES

    for kind in ("families", "incidents", "opportunities", "experiments", "actions"):
        result = await db.execute_system(f"SELECT COUNT(*) FROM NOVA_SYSTEM.{TABLES[kind]}")
        counts[kind] = int(result["rows"][0][0])
    return {
        "policy": policy,
        "counts": counts,
        "collection": collector.health(),
        "collection_scope": "this_backend_process",
        "latency_basis": "nova_total_ms",
        "engine_time_availability": "requires_profile_evidence",
    }


@router.get("/policies")
async def policy(user: Reader):
    return await service.policy()


@router.put("/policies")
async def update_policy(body: Policy, user: Administrator):
    try:
        return await service.save_policy(body, user)
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.put("/enrollments/{identifier}")
async def update_enrollment(identifier: str, body: Enrollment, user: Administrator):
    if identifier != body.id:
        raise HTTPException(400, "Enrollment identity does not match the path")
    try:
        return await service.save_enrollment(body, user)
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        await service.audit(
            user, "ENROLL", identifier, status="DENIED", reason="enrollment_validation_failed"
        )
        raise HTTPException(400, str(exc)) from None


@router.post("/opportunities", status_code=201)
async def propose(body: Candidate, user: Administrator):
    try:
        return await service.save_candidate(body, user)
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/opportunities/{identifier}/{operation}", status_code=202)
async def mutate(
    identifier: str,
    operation: Literal["experiment", "approve", "reject", "apply"],
    body: Mutation,
    user: Administrator,
):
    try:
        return await service.mutate(
            identifier,
            operation,
            version=body.candidate_version,
            idempotency_key=body.idempotency_key,
            user=user,
        )
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        await service.audit(
            user,
            operation.upper(),
            identifier,
            status="DENIED",
            reason="operation_validation_failed",
        )
        raise HTTPException(400, str(exc)) from None


@router.get("/{collection}")
async def listing(
    collection: Collection,
    user: Reader,
    after: str = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    family_id: str | None = None,
    cohort_id: str | None = None,
):
    rows = await repository.page(
        collection, after=after, limit=limit + 1, family_id=family_id, cohort_id=cohort_id
    )
    return {
        "items": [public_record(row) for row in rows[:limit]],
        "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None,
    }


@router.get("/{collection}/{identifier}")
async def detail(collection: Collection, identifier: str, user: Reader):
    record = await repository.get(collection, identifier)
    if record is None:
        raise HTTPException(404, "Record not found")
    return public_record(record)
