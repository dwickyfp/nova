"""NDV-OPS-006: Native Dataviz operator metrics have bounded labels and no private values."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from prometheus_client import REGISTRY, generate_latest

from app.core.exceptions import NovaException
from app.modules.viz import dataset_service as datasets_module
from app.modules.viz import scheduled_export
from app.observability import metrics
from app.observability.metrics import (
    DATAVIZ_EXPORT_OUTCOMES,
    DATAVIZ_OPERATIONS,
    DATAVIZ_OUTCOMES,
    dataviz_export,
    dataviz_outcome,
    dataviz_read,
)

USER = {"username": "private-person-4421", "active_role": "PRIVATE_ROLE_77",
        "session_id": "session-secret", "security_context_version": 1}


def _samples(prefix: str) -> list[tuple[str, dict[str, str], float]]:
    return [(sample.name, sample.labels, sample.value)
            for family in REGISTRY.collect() if family.name.startswith(prefix)
            for sample in family.samples]


def _count(operation: str, outcome: str) -> float:
    return REGISTRY.get_sample_value(
        "nova_dataviz_reads_total", {"operation": operation, "outcome": outcome}) or 0.0


@pytest.mark.parametrize(("error", "outcome"), [
    (None, "ok"),
    (NovaException("x", status_code=403), "refused"),
    (NovaException("x", status_code=404), "refused"),
    (NovaException("x", status_code=422), "invalid"),
    (NovaException("x", status_code=429), "rejected"),
    (NovaException("x", status_code=504), "timeout"),
    (asyncio.CancelledError(), "cancelled"),
    (RuntimeError("engine said private_table"), "error"),
])
def test_every_outcome_is_one_of_a_closed_set(error, outcome):
    assert dataviz_outcome(error) == outcome and outcome in DATAVIZ_OUTCOMES


def test_an_unknown_operation_or_export_outcome_is_refused():
    with pytest.raises(ValueError), dataviz_read("private-dashboard-name"):
        pass
    with pytest.raises(ValueError):
        dataviz_export("private")


@pytest.mark.asyncio
async def test_queries_are_counted_by_outcome_without_private_labels(monkeypatch):
    service = datasets_module.NativeDatasetService()
    identity = uuid4()

    async def answered(user, dataset, request):
        return "rows"

    async def refused(user, dataset, request):
        raise NovaException("Dataset is unavailable or not permitted", status_code=403)

    before_ok, before_refused = _count("query", "ok"), _count("query", "refused")
    monkeypatch.setattr(service, "_admitted_query", answered)
    assert await service.query(USER, identity, SimpleNamespace()) == "rows"
    monkeypatch.setattr(service, "_admitted_query", refused)
    with pytest.raises(NovaException):
        await service.query(USER, identity, SimpleNamespace())
    monkeypatch.setattr(service, "_admitted_preview", refused)
    with pytest.raises(NovaException):
        await service.preview(USER, identity, SimpleNamespace())
    assert _count("query", "ok") == before_ok + 1
    assert _count("query", "refused") == before_refused + 1
    assert _count("preview", "refused") >= 1

    exposition = generate_latest(REGISTRY).decode()
    for private in (str(identity), USER["username"], USER["active_role"], "session-secret",
                    "not permitted"):
        assert private not in exposition
    for name, labels, _ in _samples("nova_dataviz_read"):
        assert set(labels) <= {"operation", "outcome", "le"}, name
        assert labels["operation"] in DATAVIZ_OPERATIONS
        assert labels["outcome"] in DATAVIZ_OUTCOMES


@pytest.mark.asyncio
async def test_scheduled_exports_are_counted_by_outcome(monkeypatch):
    def exported(outcome: str) -> float:
        return REGISTRY.get_sample_value(
            "nova_dataviz_scheduled_exports_total", {"outcome": outcome}) or 0.0

    before = {outcome: exported(outcome) for outcome in DATAVIZ_EXPORT_OUTCOMES}
    results = iter([{"status": "saved", "existing": False}, {"status": "saved", "existing": True},
                    scheduled_export.ExportUnavailable("no Export"), RuntimeError("disk")])

    async def run(record_id, user, *, occurrence=None, now=None):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(scheduled_export, "_run", run)
    await scheduled_export.run("record", USER)
    await scheduled_export.run("record", USER)
    with pytest.raises(scheduled_export.ExportUnavailable):
        await scheduled_export.run("record", USER)
    with pytest.raises(RuntimeError):
        await scheduled_export.run("record", USER)
    for outcome in DATAVIZ_EXPORT_OUTCOMES:
        assert exported(outcome) == before[outcome] + 1, outcome
    for _, labels, _ in _samples("nova_dataviz_scheduled_exports"):
        assert set(labels) <= {"outcome"} and labels["outcome"] in DATAVIZ_EXPORT_OUTCOMES


def test_metrics_module_exports_the_dataviz_families():
    assert metrics.DATAVIZ_READS._name == "nova_dataviz_reads"
