"""Decision approval rules layered after data authorization and tool consent."""

from __future__ import annotations

import json
from typing import Literal

from fastapi import HTTPException
from pydantic import Field

from app.common.audit import write_audit_log
from app.core.database import db
from app.modules.intelligence.contracts import Contract, DecisionOption, PolicyResult, fingerprint
from app.modules.intelligence.engine_repository import metadata_lock

BUSINESS_POLICY_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_BUSINESS_POLICIES (
    id VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    definition JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(id, revision)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""


class BusinessPolicy(Contract):
    id: Literal["default"] = "default"
    revision: int = Field(default=1, ge=1)
    maximum_cost: float = Field(default=0, ge=0)
    automatic_cost_limit: float = Field(default=0, ge=0)
    reviewer_roles: list[str] = Field(default_factory=lambda: ["ACCOUNTADMIN"], max_length=32)
    blocked_actions: list[str] = Field(default_factory=list, max_length=32)
    allow_high_risk: bool = False


async def read_business_policy() -> BusinessPolicy:
    result = await db.execute_system(
        "SELECT definition FROM NOVA_SYSTEM.CONFIG_BUSINESS_POLICIES "
        "WHERE id='default' OR id LIKE 'default:%' ORDER BY revision DESC LIMIT 2"
    )
    if not result["rows"]:
        return BusinessPolicy()
    if len(result["rows"]) > 1:
        records = [
            BusinessPolicy.model_validate(json.loads(row[0]) if isinstance(row[0], str) else row[0])
            for row in result["rows"]
        ]
        if records[0].revision == records[1].revision:
            raise HTTPException(
                status_code=409, detail="Concurrent policies require reconciliation"
            )
    value = result["rows"][0][0]
    return BusinessPolicy.model_validate(json.loads(value) if isinstance(value, str) else value)


async def save_business_policy(value: BusinessPolicy, user: dict) -> BusinessPolicy:
    if user.get("active_role") != "ACCOUNTADMIN":
        raise HTTPException(status_code=403, detail="ACCOUNTADMIN is required to configure policy")
    if value.automatic_cost_limit > value.maximum_cost:
        raise HTTPException(status_code=422, detail="Automatic limit exceeds the maximum cost")
    async with metadata_lock("business-policy:default") as lock:
        current = await read_business_policy()
        if current.model_dump(exclude={"revision"}) == value.model_dump(exclude={"revision"}):
            return current
        if current.revision != value.revision:
            raise HTTPException(status_code=409, detail="Policy changed; reload before editing")
        value = value.model_copy(update={"revision": value.revision + 1})
        if not await lock.renew():
            raise HTTPException(status_code=409, detail="Policy lease expired; retry")
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_BUSINESS_POLICIES "
            "(id,revision,definition,created_at) VALUES (%s,%s,%s,NOW())",
            [
                "default:" + fingerprint(value.model_dump(mode="json"))[:32],
                value.revision,
                value.model_dump_json(),
            ],
        )
    await write_audit_log(
        event_type="BUSINESS_POLICY",
        user_name=user["username"],
        action="UPDATE",
        object_type="BUSINESS_POLICY",
        object_name="default",
        status="SUCCESS",
        session_id=user.get("session_id"),
        active_role=user.get("active_role"),
    )
    return value


def evaluate_business_policy(
    option: DecisionOption, policy: BusinessPolicy, context: dict
) -> PolicyResult:
    if (
        not option.feasible
        or option.action_type in policy.blocked_actions
        or option.cost > policy.maximum_cost
        or (option.risk == "high" and not policy.allow_high_risk)
    ):
        decision, reason = (
            "DENY",
            "The option exceeds a configured cost, feasibility, or risk limit",
        )
    elif option.action_type == "recommendation" and option.cost <= policy.automatic_cost_limit:
        decision, reason = "ALLOW", "The recommendation is within the automatic review limit"
    else:
        decision, reason = "REQUIRE_APPROVAL", "An authorized reviewer must approve this revision"
    return PolicyResult(
        decision=decision,
        reason=reason,
        policy_id=policy.id,
        policy_revision=policy.revision,
        context_digest=fingerprint(
            {
                "context": context,
                "option": option.model_dump(mode="json"),
                "policy": policy.model_dump(mode="json"),
            }
        ),
    )
