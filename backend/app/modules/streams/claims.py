"""Durable arbitration of one immutable stream operation per generation.

Label uniqueness serializes simultaneous INSERTs. The durable existence guard
preserves the winner after label retention expires. A recovered winner row is
evidence for verification only; it never grants permission to submit target DML.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum


class ClaimOutcome(StrEnum):
    ACQUIRED = "ACQUIRED"
    EXISTING = "EXISTING"
    VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"


@dataclass(frozen=True, slots=True)
class Claim:
    resource_id: str
    epoch: int
    generation: int
    attempt_id: str
    operation_digest: str

    def __post_init__(self) -> None:
        for value in (self.resource_id, self.attempt_id, self.operation_digest):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError("Claim identities must be SHA-256 hex digests")
        if not 0 <= self.epoch < 2**63 or not 0 <= self.generation < 2**63:
            raise ValueError("Claim epoch and generation must be nonnegative BIGINTs")

    @property
    def key(self) -> str:
        return hashlib.sha256(
            f"{self.resource_id}:{self.epoch}:{self.generation}".encode()
        ).hexdigest()

    @property
    def label(self) -> str:
        return "nova_stream_claim_" + self.key


@dataclass(frozen=True, slots=True)
class ClaimResult:
    outcome: ClaimOutcome
    winner: Claim | None = None


MetadataExecutor = Callable[[str, tuple], Awaitable[dict]]

CLAIMS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT_STREAM_CLAIMS (
    claim_key VARCHAR(64) NOT NULL,
    resource_id VARCHAR(64) NOT NULL,
    epoch BIGINT NOT NULL,
    generation BIGINT NOT NULL,
    attempt_id VARCHAR(64) NOT NULL,
    operation_digest VARCHAR(64) NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(claim_key)
DISTRIBUTED BY HASH(claim_key) BUCKETS 1
PROPERTIES ('replication_num'='1')
"""


class ClaimRepository:
    def __init__(self, execute: MetadataExecutor | None = None) -> None:
        if execute is None:
            from app.core.database import db

            execute = db.execute_system
        self._execute = execute

    async def inspect(self, claim: Claim) -> ClaimResult:
        try:
            result = await self._execute(
                "SELECT resource_id, epoch, generation, attempt_id, operation_digest "
                "FROM NOVA_SYSTEM.AUDIT_STREAM_CLAIMS WHERE claim_key = %s LIMIT 2",
                (claim.key,),
            )
            rows = result["rows"]
            if len(rows) != 1:
                return ClaimResult(ClaimOutcome.VERIFICATION_REQUIRED)
            winner = Claim(*rows[0])
            if winner.key != claim.key:
                return ClaimResult(ClaimOutcome.VERIFICATION_REQUIRED)
        except Exception:
            return ClaimResult(ClaimOutcome.VERIFICATION_REQUIRED)
        return ClaimResult(ClaimOutcome.EXISTING, winner)

    async def acquire(self, claim: Claim) -> ClaimResult:
        try:
            result = await self._execute(
                "INSERT INTO NOVA_SYSTEM.AUDIT_STREAM_CLAIMS "
                f"WITH LABEL {claim.label} "
                "SELECT %s, %s, %s, %s, %s, %s, NOW() "
                "WHERE NOT EXISTS (SELECT 1 FROM NOVA_SYSTEM.AUDIT_STREAM_CLAIMS "
                "WHERE claim_key = %s)",
                (
                    claim.key, claim.resource_id, claim.epoch, claim.generation,
                    claim.attempt_id, claim.operation_digest, claim.key,
                ),
            )
        except Exception:
            # Lost INSERT response and duplicate label both require inspection.
            # Even our own attempt_id does not prove we still own submission.
            return await self.inspect(claim)
        inspected = await self.inspect(claim)
        if result.get("affected") == 1 and inspected.winner == claim:
            return ClaimResult(ClaimOutcome.ACQUIRED, claim)
        return inspected
