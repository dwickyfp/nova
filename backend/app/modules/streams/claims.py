"""Durable arbitration of one immutable stream operation per generation.

The first claim of a key wins in PostgreSQL; a later claimant inserts nothing
and reads the winner. A recovered winner row is evidence for verification only;
it never grants permission to submit target DML.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from enum import StrEnum

from app.modules.streams.control import MetadataScope, stream_metadata

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True, slots=True)
class ClaimResult:
    outcome: ClaimOutcome
    winner: Claim | None = None


class ClaimRepository:
    def __init__(self, metadata: MetadataScope = stream_metadata) -> None:
        self._metadata = metadata

    async def inspect(self, claim: Claim) -> ClaimResult:
        try:
            async with self._metadata(read_only=True) as repository:
                row = await repository.inspect_claim(claim.key)
            if row is None:
                return ClaimResult(ClaimOutcome.VERIFICATION_REQUIRED)
            winner = Claim(
                row["resource_id"], int(row["epoch"]), int(row["generation"]),
                row["attempt_id"], row["operation_digest"],
            )
            if winner.key != claim.key:
                return ClaimResult(ClaimOutcome.VERIFICATION_REQUIRED)
        except Exception:  # noqa: BLE001 - unknown evidence is never a decision
            logger.debug("stream claim inspection failed", exc_info=True)
            return ClaimResult(ClaimOutcome.VERIFICATION_REQUIRED)
        return ClaimResult(ClaimOutcome.EXISTING, winner)

    async def acquire(self, claim: Claim) -> ClaimResult:
        values = {
            "resource_id": claim.resource_id, "epoch": claim.epoch,
            "generation": claim.generation, "attempt_id": claim.attempt_id,
            "operation_digest": claim.operation_digest,
        }
        try:
            async with self._metadata() as repository:
                _winner, inserted = await repository.acquire_claim(values)
        except Exception:  # noqa: BLE001 - a lost commit response needs inspection
            # Even our own attempt_id does not prove we still own submission.
            return await self.inspect(claim)
        inspected = await self.inspect(claim)
        if inserted and inspected.winner == claim:
            return ClaimResult(ClaimOutcome.ACQUIRED, claim)
        return inspected
