"""Bounded row drill through the existing semantic compiler and caller context."""

from fastapi import HTTPException
from pydantic import Field

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.intelligence.semantic_views import semantic_view_service
from app.modules.smart_reports.schemas import Contract, FieldName, Identifier, Predicate, Scalar
from app.modules.smart_reports.service import report_service


class DetailRequest(Contract):
    view_session_id: Identifier
    expected_state_revision: int = Field(ge=1)
    field: FieldName
    values: list[Scalar] = Field(min_length=1, max_length=50)
    page_index: int = Field(default=0, ge=0, le=19)


def validate_detail_fields(model: SemanticModelIR, fields: list[str]) -> None:
    selected = [model.field(name) for name in fields]
    if any(field is None or field.kind.value != "dimension" for field in selected):
        raise ValueError("Detail requires published dimension fields")
    datasets = {field.dataset for field in selected if field}
    if len(datasets) != 1:
        raise ValueError("Detail fields must share one declared dataset grain")
    dataset = model.dataset(next(iter(datasets)))
    names = {field.name for field in selected if field}
    if not dataset or not dataset.grain.keys or not set(dataset.grain.keys) <= names:
        raise ValueError("Detail must explicitly include the published grain keys")


async def read_detail(report_id: str, widget_id: str, body: DetailRequest, user: dict) -> dict:
    from app.modules.smart_reports.runtime import report_runtime

    spec, state = await report_service.view(report_id, body.view_session_id, user)
    if state.state_revision != body.expected_state_revision:
        raise HTTPException(409, "Report state changed; reopen row details")
    original_state = state
    spec, state = spec.model_copy(deep=True), state.model_copy(deep=True)
    widget = spec.widgets().get(widget_id)
    if not widget or not widget.query or not widget.detail_fields:
        raise HTTPException(422, "Row detail is not configured for this widget")
    grouping = state.group_by.get(widget_id, widget.query.dimensions)
    if body.field not in grouping:
        raise HTTPException(422, "Choose a displayed grouping before opening row detail")
    source = next(s for s in spec.sources if s.id == widget.query.source)
    _, version = await semantic_view_service._readable_version(source.view_id, source.version, user)
    validate_detail_fields(SemanticModelIR.from_ossie(version["definition"]), widget.detail_fields)
    widget.kind = "table"
    widget.query.metrics = []
    widget.query.calculations = []
    widget.query.transforms = []
    widget.query.order_by = []
    widget.query.dimensions = widget.detail_fields
    if widget.query.time:
        widget.query.time.grain = None
        widget.query.time.compare = None
    state.group_by.pop(widget_id, None)
    state.sort.pop(widget_id, None)
    state.drill_paths.setdefault(widget_id, []).append(
        Predicate(field=body.field, operator="IN", value=body.values)
    )
    state.page_size = 100
    state.page_index[widget_id] = body.page_index
    result = await report_runtime.render(spec, state, user, [widget_id])
    result["detail_limit"] = 2000
    result["page_index"] = body.page_index
    await report_service.verify_view(original_state, user)
    await report_service.audit(report_id, user, "detail")
    return result
