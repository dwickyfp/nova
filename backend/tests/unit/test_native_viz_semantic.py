"""Native datasets retain the published semantic owner's plan and authority."""

from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.exceptions import NovaException
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.query.repository import QueryResult
from app.modules.viz import dataset_service as module
from app.modules.viz.contracts.authoring import (
    DatasetPreviewRequest,
    SemanticDatasetCreate,
    SemanticInspect,
)
from app.modules.viz.contracts.datasets import DatasetDefinition, SemanticSelection
from app.sql_frontend.binding.models import BoundColumn
from tests.unit.test_native_viz_datasets import schema_result
from tests.unit.test_native_viz_datasets import setup as setup
from tests.unit.test_native_viz_datasets import source as source


@pytest.fixture
def semantic(setup, monkeypatch):
    definition = {
        "name": "Orders", "version": "1.0",
        "datasets": [{"name": "orders", "source": "analytics.orders",
                      "grain": {"keys": ["id"]}, "fields": [
                          {"name": "id", "expression": "id", "kind": "dimension"},
                          {"name": "amount", "expression": "amount", "kind": "fact"},
                      ]}],
        "metrics": [{"name": "total", "expression": "SUM(orders.amount)",
                     "base_dataset": "orders", "unit": "IDR"}],
    }
    model = SemanticModelIR.from_ossie(definition)
    record = {"definition": definition, "database_name": "analytics"}
    owner = AsyncMock(return_value=record)
    monkeypatch.setattr(module.semantic_view_service, "get_version_for_agent", owner)
    columns = (BoundColumn("id", "bigint", 1, False),
               BoundColumn("total", "decimal(38,12)", 2, True))
    setup.execute.return_value = schema_result(columns)
    request = SemanticInspect(dataset_id=uuid4(), source=SemanticSelection(
        model_id=str(uuid4()), model_revision=3, model_fingerprint=model.fingerprint,
        dimensions=("id",), metrics=("total",),
    ))
    return SimpleNamespace(**vars(setup), owner=owner, record=record,
                           columns=columns, request=request)


async def test_semantic_inspection_uses_existing_compiler_and_analyzer(semantic):
    case = semantic
    result = await case.service.inspect(case.user, case.request)
    assert result.source.model_revision == 3
    assert result.source.model_fingerprint == case.request.source.model_fingerprint
    assert result.source.dependencies[0].physical_id == "10081"
    assert [f.semantic_role for f in result.fields] == ["dimension", "measure"]
    assert result.fields[1].unit == "IDR"
    assert result.fields[1].physical_type == "decimal(38,12)"
    assert "sql" not in result.source.model_dump()
    sql = case.execute.call_args.kwargs["sql"]
    assert sql.startswith("EXPLAIN SCHEMA SELECT")
    assert "SUM(" in sql and "GROUP BY" in sql
    assert case.owner.await_count == 2
    for call in case.owner.call_args_list:
        assert call.args[:3] == (case.request.source.model_id, 3,
                                 case.request.source.model_fingerprint)
        assert call.args[3]["username"] == "alice"
        assert call.args[3]["active_role"] == "ANALYST"
        assert not call.kwargs  # Native sharing never delegates an agent owner's private view.
    assert result == await case.service.inspect(case.user, case.request)


@pytest.mark.parametrize("status", [403, 404, 409])
async def test_denied_or_missing_pinned_model_never_queries_data(semantic, status):
    semantic.owner.side_effect = HTTPException(status, "private model detail")
    with pytest.raises(NovaException) as error:
        await semantic.service.inspect(semantic.user, semantic.request)
    assert error.value.status_code == (409 if status == 409 else 404)
    assert "private model detail" not in str(error.value)
    semantic.execute.assert_not_awaited()


async def test_model_revoked_during_inspection_discards_schema(semantic):
    semantic.owner.side_effect = [semantic.record, HTTPException(404, "revoked")]
    with pytest.raises(NovaException) as error:
        await semantic.service.inspect(semantic.user, semantic.request)
    assert error.value.status_code == 404
    assert semantic.execute.await_count == 1


async def test_unknown_metric_is_rejected_by_shared_compiler(semantic):
    request = semantic.request.model_copy(update={
        "source": semantic.request.source.model_copy(update={"metrics": ("missing",)})
    })
    with pytest.raises(NovaException) as error:
        await semantic.service.inspect(semantic.user, request)
    assert error.value.status_code == 422
    semantic.execute.assert_not_awaited()


async def test_corrupt_pinned_model_is_refused_without_querying(semantic):
    semantic.record["definition"]["metrics"][0]["expression"] = "SUM(orders.id)"
    with pytest.raises(NovaException) as error:
        await semantic.service.inspect(semantic.user, semantic.request)
    assert error.value.status_code == 409
    semantic.execute.assert_not_awaited()


async def test_invalid_semantic_metadata_does_not_expose_private_exception(semantic):
    semantic.owner.side_effect = ValueError("private serialized definition")
    with pytest.raises(NovaException) as error:
        await semantic.service.inspect(semantic.user, semantic.request)
    assert error.value.status_code == 422
    assert "private serialized definition" not in str(error.value)
    semantic.execute.assert_not_awaited()


async def test_authoritative_projection_must_match_compiled_names(semantic):
    semantic.execute.return_value = schema_result((BoundColumn("wrong", "bigint", 1, False),))
    with pytest.raises(NovaException) as error:
        await semantic.service.inspect(semantic.user, semantic.request)
    assert error.value.status_code == 422


async def test_rebound_physical_source_requires_new_field_identity(semantic):
    first = await semantic.service.inspect(semantic.user, semantic.request)
    semantic.provider.get_source_schema.return_value = replace(
        semantic.provider.get_source_schema.return_value, physical_id="replaced"
    )
    second = await semantic.service.inspect(semantic.user, semantic.request)
    assert first.fields[0].field_id != second.fields[0].field_id
    assert first.schema_fingerprint != second.schema_fingerprint


async def test_create_keeps_semantic_io_outside_pg_and_persists_declared_grain(
    semantic, monkeypatch,
):
    case = semantic
    inspected = await case.service.inspect(case.user, case.request)
    repository = SimpleNamespace(create=AsyncMock(return_value={"new_revision": 1}),
                                 dataset_intent_receipt=AsyncMock(return_value=None))
    in_pg = False
    async def load(*args):
        assert not in_pg
        return case.record
    case.owner.side_effect = load

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        nonlocal in_pg
        in_pg = True
        try:
            yield repository
        finally:
            in_pg = False

    monkeypatch.setattr(module, "document_transaction", transaction)
    monkeypatch.setattr(module, "document_workspace",
                        AsyncMock(return_value=SimpleNamespace(id="space")))
    request = SemanticDatasetCreate(**case.request.model_dump(), title="Totals",
                                   workspace_id="space",
                                   expected_schema_fingerprint=inspected.schema_fingerprint)
    result = await case.service.create(case.user, request, operation_id=uuid4())
    document = repository.create.call_args.args[1]
    assert document.grain == (inspected.fields[0].field_id,)
    assert document.source == inspected.source
    assert result["new_revision"] == 1


async def test_semantic_preview_revalidates_model_and_preserves_decimal(semantic, monkeypatch):
    case = semantic
    inspected = await case.service.inspect(case.user, case.request)
    document = DatasetDefinition(**inspected.model_dump(), title="Totals")
    snapshot = dict(revision=1, metadata_revision=1, grants_revision=1,
                    publication_id=None, definition=document)
    repository = SimpleNamespace(require_kind=AsyncMock(), read=AsyncMock(return_value=snapshot))

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield repository

    monkeypatch.setattr(module, "document_transaction", transaction)
    async def execute(**kwargs):
        if "row_sink" not in kwargs:
            return schema_result(case.columns)
        assert kwargs["role"] == "ANALYST" and kwargs["username"] == "alice"
        assert kwargs["max_rows"] == 3
        sink = kwargs["row_sink"]
        await sink.begin(["id", "total"], ())
        await sink.rows([[1, Decimal("99999999999999999999999999.123456789012")]])
        return QueryResult(columns=["id", "total"], streamed=True, row_count=1)
    case.execute.side_effect = execute
    case.owner.reset_mock()
    result = await case.service.preview(case.user, document.dataset_id,
                                        DatasetPreviewRequest(revision=1, limit=2))
    assert result.rows[0][1].value == "99999999999999999999999999.123456789012"
    assert not result.has_more
    assert case.owner.await_count == 5
    assert "sql" not in result.model_dump()


@pytest.mark.parametrize("update", [
    {"dimensions": ("TOTAL",)}, {"metrics": ("total", "total")},
    {"named_filters": ("paid", "paid")}, {"metrics": ()},
    {"release_id": "invented"}, {"model_fingerprint": "stale"},
])
def test_semantic_selection_rejects_ambiguous_or_invented_contracts(semantic, update):
    with pytest.raises(ValidationError):
        SemanticSelection.model_validate({**semantic.request.source.model_dump(), **update})
