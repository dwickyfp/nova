"""Smart Report contracts, deterministic state and access-bound execution."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.planning import SemanticFilter, SemanticOrder, SemanticPlan
from app.modules.smart_reports.content import public_report_block
from app.modules.smart_reports.migration import replacement_spec
from app.modules.smart_reports.patches import apply_patch
from app.modules.smart_reports.repository import decode, digest
from app.modules.smart_reports.runtime import ReportRuntime, scalar_json, widget_plan
from app.modules.smart_reports.schemas import (
    FilterValue,
    ReportInteractionEvent,
    ReportPatch,
    ReportSpecV1,
    ReportViewStateV1,
)
from app.modules.smart_reports.security import require_current_session
from app.modules.smart_reports.sessions import reduce_event, scope
from tests.unit.test_semantic_intelligence import sales_model

USER = {
    "username": "alice",
    "session_id": "session-a",
    "active_role": "analyst",
    "assigned_roles": ["analyst", "publisher"],
    "security_context_version": 1,
}


def report_spec():
    return ReportSpecV1.model_validate(
        {
            "title": "City revenue",
            "sources": [
                {"id": "sales", "view_id": "sales-view", "version": 1, "fingerprint": "abc"}
            ],
            "filters": [
                {
                    "id": "city",
                    "label": "City",
                    "source": "sales",
                    "field": "region",
                    "kind": "single",
                    "default": {"kind": "single", "values": ["Jakarta"]},
                }
            ],
            "pages": [
                {
                    "id": "overview",
                    "title": "Overview",
                    "sections": [
                        {
                            "id": "main",
                            "widgets": [
                                {
                                    "id": "revenue",
                                    "title": "Revenue",
                                    "kind": "kpi",
                                    "query": {"source": "sales", "metrics": ["total_revenue"]},
                                    "placement": {"col": 0, "row": 0, "width": 12, "height": 1},
                                },
                                {
                                    "id": "cities",
                                    "title": "Cities",
                                    "kind": "table",
                                    "query": {
                                        "source": "sales",
                                        "metrics": ["total_revenue"],
                                        "dimensions": ["region"],
                                    },
                                    "placement": {"col": 0, "row": 1, "width": 12, "height": 2},
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    )


def view(spec=None):
    spec = spec or report_spec()
    return ReportViewStateV1(
        session_id="view-a",
        report_id="report-a",
        spec_revision=1,
        active_page_id="overview",
        filters={f.id: f.default for f in spec.filters},
    )


def event(**kwargs):
    return ReportInteractionEvent(
        event_id="event-a", expected_state_revision=1, spec_revision=1, **kwargs
    )


@pytest.mark.parametrize("extra", [{"owner": "root"}, {"revision": 9}, {"published_revision": 4}])
def test_server_fields_cannot_enter_definition(extra):
    with pytest.raises(ValidationError):
        ReportSpecV1.model_validate({**report_spec().model_dump(), **extra})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s["pages"][0]["sections"][0]["widgets"][1]["placement"].update(row=0),
        lambda s: s["pages"][0]["sections"][0]["widgets"][0]["query"].update(source="missing"),
        lambda s: s["filters"][0].update(parents=["city"]),
        lambda s: s.update(timezone="not/a-zone"),
    ],
)
def test_invalid_references_layout_and_cycles_are_rejected(mutate):
    data = report_spec().model_dump()
    mutate(data)
    with pytest.raises(ValidationError):
        ReportSpecV1.model_validate(data)


def test_filter_transition_changes_query_without_mutating_spec():
    spec = report_spec()
    original = spec.model_dump()
    initial = view(spec)
    changed = reduce_event(
        spec,
        initial,
        event(
            type="FILTER_SET", target="city", value=FilterValue(kind="single", values=["Bandung"])
        ),
    )
    plan, excluded = widget_plan(spec, changed, spec.widgets()["revenue"], datetime.now(UTC))
    assert plan.filters == (SemanticFilter("region", "=", "Bandung"),)
    assert plan.limit is None
    assert excluded == []
    assert initial.filters["city"].values == ["Jakarta"]
    assert changed.state_revision == 2
    assert spec.model_dump() == original


def test_two_tabs_cannot_overwrite_newer_state():
    initial = view()
    changed = reduce_event(report_spec(), initial, event(type="FILTER_RESET_ALL"))
    with pytest.raises(HTTPException) as caught:
        reduce_event(report_spec(), changed, event(type="FILTER_RESET_ALL"))
    assert caught.value.status_code == 409


def test_unset_and_empty_multiselect_are_distinct():
    raw = report_spec().model_dump()
    raw["filters"][0].update(kind="multi", default={"kind": "multi", "values": []})
    spec = ReportSpecV1.model_validate(raw)
    plan, _ = widget_plan(spec, view(spec), spec.widgets()["revenue"], datetime.now(UTC))
    sql = SemanticCompiler().compile(sales_model(), plan).sql
    assert "FALSE" in sql
    changed = reduce_event(
        spec, view(spec), event(type="FILTER_SET", target="city", value=FilterValue())
    )
    plan, _ = widget_plan(spec, changed, spec.widgets()["revenue"], datetime.now(UTC))
    assert not plan.filters


def test_required_incompatible_filter_fails_closed():
    spec = report_spec()
    spec.filters[0].required = True
    spec.filters[0].source = "other"
    with pytest.raises(ValueError, match="required filter"):
        widget_plan(spec, view(spec), spec.widgets()["revenue"], datetime.now(UTC))


def test_optional_incompatible_filter_is_reported():
    spec = report_spec()
    spec.filters[0].source = "other"
    plan, excluded = widget_plan(spec, view(spec), spec.widgets()["revenue"], datetime.now(UTC))
    assert not plan.filters
    assert excluded == ["city"]


def test_table_pagination_is_stable_and_has_lookahead():
    spec = report_spec()
    state = view(spec)
    state.page_index["cities"] = 2
    plan, _ = widget_plan(spec, state, spec.widgets()["cities"], datetime.now(UTC))
    assert plan.limit == 51 and plan.offset == 100
    assert plan.order_by == (SemanticOrder("region", "asc"),)
    assert "OFFSET 100" in SemanticCompiler().compile(sales_model(), plan).sql


def test_null_set_filters_do_not_drop_nulls():
    compiler = SemanticCompiler()
    plan = SemanticPlan(
        metrics=("total_revenue",), filters=(SemanticFilter("region", "IN", [None, "East"]),)
    )
    sql = compiler.compile(sales_model(), plan).sql
    assert "IS NULL" in sql and "'East'" in sql


def test_unordered_offset_is_rejected():
    with pytest.raises(ValueError):
        SemanticCompiler().compile(
            sales_model(), SemanticPlan(metrics=("total_revenue",), limit=50, offset=50)
        )


def test_distinct_dimension_plan_roundtrip_and_rejection():
    plan = SemanticPlan.from_dict({"dimensions": ["region"], "distinct": True})
    assert SemanticPlan.from_dict(plan.as_dict()) == plan
    assert SemanticCompiler().compile(sales_model(), plan).sql.startswith("SELECT DISTINCT\n")
    assert "distinct" not in SemanticPlan(dimensions=("region",)).as_dict()
    for invalid in ("true", 1, None):
        with pytest.raises(ValueError, match="boolean"):
            SemanticCompiler().compile(
                sales_model(),
                SemanticPlan.from_dict({"dimensions": ["region"], "distinct": invalid}),
            )
    with pytest.raises(ValueError, match="dimension-only"):
        SemanticCompiler().compile(
            sales_model(), SemanticPlan(metrics=("total_revenue",), distinct=True)
        )


def test_patch_is_immutable_and_forbids_authority_changes():
    spec = report_spec()
    edited = apply_patch(spec, [ReportPatch(op="SET_REPORT_METADATA", value={"title": "Changed"})])
    assert edited.title == "Changed" and spec.title == "City revenue"
    with pytest.raises(ValueError):
        apply_patch(spec, [ReportPatch(op="SET_REPORT_METADATA", value={"owner": "root"})])
    with pytest.raises(ValidationError):
        ReportPatch.model_validate({"op": "RUN_SQL", "value": {"sql": "SELECT 1"}})


def test_report_content_never_contains_rows_or_spec():
    block = public_report_block(
        {
            "report_id": "report-a",
            "draft_id": "draft-a",
            "draft_revision": 1,
            "title": "Revenue",
            "rows": [["private"]],
            "spec": report_spec().model_dump(),
        }
    )
    assert "rows" not in block and "spec" not in block


def test_decimal_transport_and_integrity():
    assert scalar_json(Decimal("9007199254740993.123456")) == "9007199254740993.123456"
    value = {"spec": report_spec().model_dump(mode="json")}
    assert decode([value, 1, digest(value)])["payload"] == value
    with pytest.raises(HTTPException):
        decode([value, 1, "forged"])


def test_view_scope_separates_users_same_role_and_role_switch():
    assert scope(USER) != scope({**USER, "username": "bob"})
    assert scope(USER) != scope({**USER, "security_context_version": 2})
    assert scope(USER) != scope({**USER, "active_role": "publisher"})


@pytest.mark.asyncio
async def test_reject_role_change_while_query_in_flight(monkeypatch):
    from app.modules.smart_reports import security

    current = {**USER, "active_role": "publisher", "security_context_version": 2}
    monkeypatch.setattr(security.session_store, "get", AsyncMock(return_value=current))
    with pytest.raises(HTTPException) as caught:
        await require_current_session(USER)
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_widget_failure_is_partial_and_totals_are_separate_engine_aggregates(monkeypatch):
    from app.modules.smart_reports import runtime

    monkeypatch.setattr(runtime, "require_current_session", AsyncMock())
    monkeypatch.setattr(
        runtime.semantic_view_service,
        "_readable_version",
        AsyncMock(return_value=({}, {"fingerprint": "abc"})),
    )
    plans = []

    async def execute(_id, _version, plan, _user, **kwargs):
        plans.append(plan)
        if not plan.dimensions:
            return {
                "columns": ["total_revenue"],
                "rows": [[Decimal("500000.125")]],
                "query_id": "all-rows",
            }
        return {
            "columns": ["region", "total_revenue"],
            "rows": [[str(i), Decimal("1.01")] for i in range(51)],
            "query_id": "page",
        }

    monkeypatch.setattr(runtime.semantic_view_service, "execute_plan", execute)
    spec = report_spec()
    result = await ReportRuntime().render(spec, view(spec), USER, [])
    table = next(w for w in result["widgets"] if w["widget_id"] == "cities")
    assert table["totals"] == {"total_revenue": "500000.125"}
    assert len(table["rows"]) == 50 and table["has_more"]
    assert any(not p.dimensions and p.limit is None for p in plans)
    spec.widgets()["revenue"].mapping_required = True
    result = await ReportRuntime().render(spec, view(spec), USER, [])
    assert result["status"] == "partial_success"
    assert result["widgets"][0]["status"] == "requires_mapping"
    assert result["widgets"][1]["status"] == "success"


def test_legacy_migration_preserves_layout_and_never_invents_queries():
    spec, issues = replacement_spec(
        "Old",
        {
            "tiles": [
                {"artifact_id": "artifact-1", "x": 1, "y": 0, "w": 2, "h": 2},
                {"artifact_id": "artifact-2", "x": 1, "y": 0, "w": 2, "h": 2},
                {},
            ]
        },
    )
    widgets = list(spec.widgets().values())
    assert widgets[0].placement.col == 2 and widgets[0].placement.width == 4
    assert len(widgets) == 3 and all(w.mapping_required and w.query is None for w in widgets)
    assert "tile_1:invalid_position" in issues
    assert "tile_2:missing_artifact" in issues


def test_malformed_legacy_source_still_has_a_replacement_draft():
    spec, issues = replacement_spec("Broken", "{invalid")
    assert len(spec.widgets()) == 1
    assert issues[0] == "invalid_layout"


def test_fixture_and_generated_schema_are_current():
    import json
    from pathlib import Path

    from app.modules.smart_reports.content import ReportBlock
    from app.modules.smart_reports.schemas import ReportContextRef, WidgetResult

    target = Path(__file__).parents[3] / "frontend/src/features/smart-reports/contracts.json"
    schemas = json.loads(target.read_text())
    for model in [
        ReportSpecV1,
        ReportViewStateV1,
        ReportInteractionEvent,
        ReportPatch,
        WidgetResult,
        ReportContextRef,
        ReportBlock,
    ]:
        assert schemas[model.__name__] == model.model_json_schema()
