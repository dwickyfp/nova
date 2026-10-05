from dataclasses import replace

import pytest

from app.modules.streams.claims import Claim, ClaimOutcome, ClaimRepository

CLAIM = Claim("a" * 64, 1, 0, "b" * 64, "c" * 64)


def result_for(claim):
    return {"rows": [[claim.resource_id, claim.epoch, claim.generation,
                      claim.attempt_id, claim.operation_digest]]}


def test_claim_key_excludes_attempt_and_operation():
    competing = replace(CLAIM, attempt_id="d" * 64, operation_digest="e" * 64)
    assert CLAIM.label == competing.label
    assert CLAIM.label != replace(CLAIM, generation=1).label
    assert CLAIM.label != replace(CLAIM, epoch=2).label


@pytest.mark.parametrize("field,value", [
    ("resource_id", "a'; SELECT 1"), ("epoch", -1), ("generation", 2**63),
    ("attempt_id", ""), ("operation_digest", "z" * 64),
])
def test_claim_validation(field, value):
    with pytest.raises(ValueError):
        replace(CLAIM, **{field: value})


async def test_only_acknowledged_insert_winner_can_submit():
    calls = []

    async def execute(sql, params):
        calls.append((sql, params))
        if sql.startswith("INSERT"):
            return {"affected": 1}
        return result_for(CLAIM)

    result = await ClaimRepository(execute).acquire(CLAIM)
    assert result.outcome == ClaimOutcome.ACQUIRED
    assert "WHERE NOT EXISTS" in calls[0][0]
    assert CLAIM.label in calls[0][0]
    assert len(calls) == 2


async def test_lost_ack_never_grants_target_submission_even_to_same_attempt():
    async def execute(sql, params):
        if sql.startswith("INSERT"):
            raise OSError("socket lost after commit")
        return result_for(CLAIM)

    result = await ClaimRepository(execute).acquire(CLAIM)
    assert result.outcome == ClaimOutcome.EXISTING
    assert result.winner == CLAIM


async def test_zero_row_insert_after_label_expiry_does_not_acquire():
    async def execute(sql, params):
        if sql.startswith("INSERT"):
            return {"affected": 0}
        return result_for(CLAIM)

    assert (await ClaimRepository(execute).acquire(CLAIM)).outcome == ClaimOutcome.EXISTING


async def test_another_winner_is_not_overwritten():
    other = replace(CLAIM, attempt_id="d" * 64)

    async def execute(sql, params):
        if sql.startswith("INSERT"):
            raise RuntimeError("label in use")
        return result_for(other)

    result = await ClaimRepository(execute).acquire(CLAIM)
    assert result == await ClaimRepository(execute).inspect(CLAIM)
    assert result.winner == other


async def test_unknown_claim_blocks():
    async def execute(sql, params):
        raise RuntimeError("private diagnostic")

    result = await ClaimRepository(execute).acquire(CLAIM)
    assert result.outcome == ClaimOutcome.VERIFICATION_REQUIRED
    assert "private" not in repr(result)
