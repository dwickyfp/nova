from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.core.metadata_db import MetadataConflict, MetadataOutcomeUnknown
from app.modules.streams.claims import Claim, ClaimOutcome, ClaimRepository
from tests.unit.stream_metadata_fakes import metadata_scope

CLAIM = Claim("a" * 64, 1, 0, "b" * 64, "c" * 64)


def row_for(claim):
    return {
        "claim_key": claim.key, "resource_id": claim.resource_id, "epoch": claim.epoch,
        "generation": claim.generation, "attempt_id": claim.attempt_id,
        "operation_digest": claim.operation_digest,
    }


def test_claim_key_excludes_attempt_and_operation():
    competing = replace(CLAIM, attempt_id="d" * 64, operation_digest="e" * 64)
    assert CLAIM.key == competing.key
    assert CLAIM.key != replace(CLAIM, generation=1).key
    assert CLAIM.key != replace(CLAIM, epoch=2).key


@pytest.mark.parametrize("field,value", [
    ("resource_id", "a'; SELECT 1"), ("epoch", -1), ("generation", 2**63),
    ("attempt_id", ""), ("operation_digest", "z" * 64),
])
def test_claim_validation(field, value):
    with pytest.raises(ValueError):
        replace(CLAIM, **{field: value})


async def test_only_inserted_winner_can_submit():
    scope, repository = metadata_scope()
    repository.acquire_claim = AsyncMock(return_value=(row_for(CLAIM), True))
    repository.inspect_claim = AsyncMock(return_value=row_for(CLAIM))
    result = await ClaimRepository(scope).acquire(CLAIM)
    assert result.outcome == ClaimOutcome.ACQUIRED
    repository.acquire_claim.assert_awaited_once()
    assert repository.acquire_claim.await_args.args[0]["attempt_id"] == CLAIM.attempt_id
    # The write and the verifying readback are separate transactions.
    assert scope.transactions == [False, True]


async def test_lost_commit_never_grants_target_submission_even_to_same_attempt():
    scope, repository = metadata_scope()
    repository.acquire_claim = AsyncMock(side_effect=MetadataOutcomeUnknown())
    repository.inspect_claim = AsyncMock(return_value=row_for(CLAIM))
    result = await ClaimRepository(scope).acquire(CLAIM)
    assert result.outcome == ClaimOutcome.EXISTING
    assert result.winner == CLAIM


async def test_existing_winner_does_not_acquire():
    scope, repository = metadata_scope()
    repository.acquire_claim = AsyncMock(return_value=(row_for(CLAIM), False))
    repository.inspect_claim = AsyncMock(return_value=row_for(CLAIM))
    assert (await ClaimRepository(scope).acquire(CLAIM)).outcome == ClaimOutcome.EXISTING


async def test_another_winner_is_not_overwritten():
    other = replace(CLAIM, attempt_id="d" * 64)
    scope, repository = metadata_scope()
    repository.acquire_claim = AsyncMock(side_effect=MetadataConflict())
    repository.inspect_claim = AsyncMock(return_value=row_for(other))
    result = await ClaimRepository(scope).acquire(CLAIM)
    assert result == await ClaimRepository(scope).inspect(CLAIM)
    assert result.winner == other


async def test_unknown_claim_blocks():
    scope, repository = metadata_scope()
    repository.acquire_claim = AsyncMock(side_effect=RuntimeError("private diagnostic"))
    repository.inspect_claim = AsyncMock(side_effect=RuntimeError("private diagnostic"))
    result = await ClaimRepository(scope).acquire(CLAIM)
    assert result.outcome == ClaimOutcome.VERIFICATION_REQUIRED
    assert "private" not in repr(result)


async def test_missing_or_mismatched_winner_requires_verification():
    scope, repository = metadata_scope()
    repository.inspect_claim = AsyncMock(return_value=None)
    assert (await ClaimRepository(scope).inspect(CLAIM)).outcome == (
        ClaimOutcome.VERIFICATION_REQUIRED
    )
    repository.inspect_claim = AsyncMock(return_value=row_for(replace(CLAIM, generation=3)))
    assert (await ClaimRepository(scope).inspect(CLAIM)).outcome == (
        ClaimOutcome.VERIFICATION_REQUIRED
    )
