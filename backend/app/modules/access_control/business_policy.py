"""Decision approval rules layered after data authorization and tool consent."""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol

from fastapi import HTTPException
from pydantic import Field

from app.common.audit import write_audit_log
from app.core.metadata_db import MetadataConflict
from app.core.metadata_runtime import metadata_db
from app.modules.access_control.metadata_repository import AccessControlMetadataRepository
from app.modules.access_control.security_context import SecurityContext
from app.modules.intelligence.contracts import Contract, PolicyResult, fingerprint

#: Legacy StarRocks DDL. Runtime reads and writes PostgreSQL only; this text is
#: kept solely because ``intelligence.engine_schema.ensure_engine_schema`` still
#: imports it, and goes when that bootstrap drops the table.
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


def _actor(user: dict | SecurityContext) -> SecurityContext:
    if isinstance(user, SecurityContext):
        return user
    return user.get("security") or SecurityContext.from_session(user)


async def _current(repo: AccessControlMetadataRepository) -> BusinessPolicy:
    try:
        row = await repo.current_business_policy()
    except MetadataConflict as exc:
        raise HTTPException(
            status_code=409, detail="Concurrent policies require reconciliation"
        ) from exc
    if row is None:
        return BusinessPolicy()
    value = row["definition"]
    return BusinessPolicy.model_validate(json.loads(value) if isinstance(value, str) else value)


async def read_business_policy(user: dict | SecurityContext) -> BusinessPolicy:
    """The business policy in force, read as ``user`` (a session or its context)."""
    actor = _actor(user)
    async with metadata_db().transaction(actor, read_only=True) as conn:
        return await _current(AccessControlMetadataRepository(conn, actor))


async def save_business_policy(value: BusinessPolicy, user: dict) -> BusinessPolicy:
    if user.get("active_role") != "ACCOUNTADMIN":
        raise HTTPException(status_code=403, detail="ACCOUNTADMIN is required to configure policy")
    if value.automatic_cost_limit > value.maximum_cost:
        raise HTTPException(status_code=422, detail="Automatic limit exceeds the maximum cost")
    actor = _actor(user)
    async with metadata_db().transaction(actor) as conn:
        repo = AccessControlMetadataRepository(conn, actor)
        current = await _current(repo)
        if current.model_dump(exclude={"revision"}) == value.model_dump(exclude={"revision"}):
            return current
        if current.revision != value.revision:
            raise HTTPException(status_code=409, detail="Policy changed; reload before editing")
        # One winner per revision: a concurrent save from the same revision is
        # refused by the store (409), never overwritten.
        row = await repo.save_business_policy(value.model_dump(mode="json"))
    saved = BusinessPolicy.model_validate(row["definition"])
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
    return saved


class BusinessPolicyInput(Protocol):
    @property
    def action_type(self) -> str: ...

    @property
    def cost(self) -> float: ...

    @property
    def risk(self) -> str: ...

    @property
    def feasible(self) -> bool: ...

    def model_dump(self, *, mode: Literal["json"]) -> dict[str, Any]: ...


def evaluate_business_policy(
    option: BusinessPolicyInput, policy: BusinessPolicy, context: dict
) -> PolicyResult:
    decision: Literal["ALLOW", "DENY", "REQUIRE_APPROVAL"]
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
