from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.common.durable_jobs import JobDefinition
from app.modules.task_orchestration.metadata_scheduler import ScheduledOccurrence, SchedulerLease


def occurrence():
    return ScheduledOccurrence(
        "legacy-root-id",
        datetime(2026, 11, 11, 12, tzinfo=UTC),
        "legacy-graph-run-id",
        JobDefinition("task", "task.graph", uuid4(), 1, "a" * 64, uuid4(), "b" * 64, False),
    )


def test_same_instant_dedupes_across_timezone_representation_and_request_nonce():
    first = occurrence()
    equivalent = replace(
        first,
        scheduled_at=first.scheduled_at.astimezone(timezone(timedelta(hours=7))),
        definition=replace(first.definition, idempotency_key=uuid4()),
    )
    assert equivalent.idempotency_key == first.idempotency_key
    assert (
        replace(first, definition=replace(first.definition, definition_version=2)).idempotency_key
        != first.idempotency_key
    )
    assert replace(first, root_task_id="other-root").idempotency_key != first.idempotency_key


@pytest.mark.parametrize("value", [datetime(2026, 11, 11), "2026-11-11", None])
def test_naive_or_unparsed_occurrence_is_refused(value):
    with pytest.raises(ValueError):
        replace(occurrence(), scheduled_at=value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("queue", "agent"),
        ("kind", "sql.execute"),
        ("external_effect", True),
    ],
)
def test_scheduled_graph_cannot_execute_external_body_in_the_orchestrator(field, value):
    original = occurrence()
    with pytest.raises(ValueError):
        replace(original, definition=replace(original.definition, **{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("root_task_id", ""),
        ("graph_run_id", "x" * 65),
        ("root_task_id", "x\x00y"),
        ("graph_run_id", "\udfff"),
    ],
)
def test_legacy_identity_validation_never_rewrites_ids(field, value):
    assert occurrence().root_task_id == "legacy-root-id"
    with pytest.raises(ValueError):
        replace(occurrence(), **{field: value})


@pytest.mark.parametrize("token", [0, -1, True, 2**63])
def test_leadership_requires_an_exact_positive_token(token):
    with pytest.raises(ValueError):
        SchedulerLease("tasks", uuid4(), token)
