"""Insights never infer findings from partial data or lose decimal precision."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.smart_reports.insights import inspect_report, observations
from app.modules.smart_reports.schemas import ReportContextRef, WidgetResult
from tests.unit.test_smart_reports import USER, report_spec, view


def test_comparison_retains_precision_and_zero_baseline_is_undefined():
    spec = report_spec()
    result = WidgetResult(
        widget_id="revenue",
        status="success",
        completeness="complete",
        columns=["total_revenue", "comparison_period"],
        rows=[["9007199254740993.12", "current_period"], ["0", "previous_period"]],
        query_fingerprint="query-a",
        semantic_version=4,
    )
    insights, excluded = observations(spec, [result])
    assert not excluded
    assert insights[0]["current"] == "9007199254740993.12"
    assert insights[0]["change"] == "9007199254740993.12"
    assert insights[0]["relative_change"] is None
    assert insights[0]["query_fingerprint"] == "query-a"
    assert insights[0]["semantic_version"] == 4


def test_largest_value_retains_ties_and_ignores_null_not_zero():
    spec = report_spec()
    result = WidgetResult(
        widget_id="cities",
        status="success",
        completeness="complete",
        columns=["region", "total_revenue"],
        rows=[["Jakarta", "-2"], ["Bandung", "-2"], ["Bogor", None], ["Medan", "-3"]],
    )
    insights, excluded = observations(spec, [result])
    assert not excluded
    assert insights[0]["categories"] == ["Jakarta", "Bandung"]
    assert insights[0]["value"] == "-2"
    assert insights[0]["group_count"] == 3
    for status, completeness in [
        ("success", "paged"),
        ("success", "truncated"),
        ("denied", "error"),
        ("empty", "complete"),
    ]:
        assert observations(
            spec,
            [
                result.model_copy(
                    update={
                        "status": status,
                        "completeness": completeness,
                    }
                )
            ],
        ) == ([], ["cities"])


async def test_stale_insight_request_never_runs_a_query(monkeypatch):
    from app.modules.smart_reports import insights

    state = view()
    monkeypatch.setattr(
        insights.report_service, "view", AsyncMock(return_value=(report_spec(), state))
    )
    render = AsyncMock()
    monkeypatch.setattr(insights.report_runtime, "render", render)
    with pytest.raises(HTTPException) as exc:
        await inspect_report(
            ReportContextRef(
                report_id=state.report_id,
                view_session_id=state.session_id,
                state_revision=state.state_revision + 1,
            ),
            USER,
        )
    assert exc.value.status_code == 409
    render.assert_not_awaited()


async def test_inflight_filter_change_rejects_old_evidence(monkeypatch):
    from app.modules.smart_reports import insights

    state = view()
    updated = state.model_copy(update={"state_revision": state.state_revision + 1})
    monkeypatch.setattr(
        insights.report_service,
        "view",
        AsyncMock(side_effect=[(report_spec(), state), (report_spec(), updated)]),
    )
    monkeypatch.setattr(insights.report_runtime, "render", AsyncMock(return_value={}))
    with pytest.raises(HTTPException) as exc:
        await inspect_report(
            ReportContextRef(
                report_id=state.report_id,
                view_session_id=state.session_id,
                state_revision=state.state_revision,
            ),
            USER,
        )
    assert exc.value.status_code == 409


async def test_report_grant_revoked_during_insight_query_rejects_delivery(monkeypatch):
    from app.modules.smart_reports import insights

    state = view()
    monkeypatch.setattr(
        insights.report_service,
        "view",
        AsyncMock(side_effect=[(report_spec(), state), HTTPException(404, "Report not found")]),
    )
    monkeypatch.setattr(insights.report_runtime, "render", AsyncMock(return_value={}))
    with pytest.raises(HTTPException) as exc:
        await inspect_report(
            ReportContextRef(
                report_id=state.report_id,
                view_session_id=state.session_id,
                state_revision=state.state_revision,
            ),
            USER,
        )
    assert exc.value.status_code == 404


def test_contribution_requires_reconciled_engine_total_and_anomaly_has_robust_evidence():
    from app.modules.agents.semantic.planning import SemanticTime

    spec = report_spec()
    result = WidgetResult(
        widget_id="cities",
        status="success",
        columns=["region", "total_revenue"],
        rows=[["Jakarta", "60"], ["Bandung", "40"]],
        totals={"total_revenue": "100"},
    )
    found, _ = observations(spec, [result])
    contribution = next(item for item in found if item["kind"] == "contribution")
    assert contribution["share"] == "0.6" and contribution["total"] == "100"
    result.totals["total_revenue"] = "80"
    assert not any(item["kind"] == "contribution" for item in observations(spec, [result])[0])
    widget = spec.widgets()["cities"]
    widget.query.time = SemanticTime("region", range="2026", grain="day")
    result.rows = [[str(i), str(value)] for i, value in enumerate([9, 10, 11, 9, 10, 11, 12, 100])]
    found, _ = observations(spec, [result])
    anomaly = next(item for item in found if item["kind"] == "unusual_value")
    assert anomaly["categories"] == ["7"] and anomaly["median"] == "10.5"
    assert anomaly["median_absolute_deviation"] == "1.0"
    assert anomaly["group_count"] == 8
    result.rows = result.rows[:7]
    assert not any(item["kind"] == "unusual_value" for item in observations(spec, [result])[0])
