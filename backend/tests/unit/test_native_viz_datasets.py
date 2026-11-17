"""Dataset authoring binds empty sources, privileges and immutable identity."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.exceptions import NovaException, StarRocksError
from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.query.admission import QueryAdmission
from app.modules.query.repository import QueryResult
from app.modules.viz import dataset_service as module
from app.modules.viz import document_service
from app.modules.viz.contracts.authoring import (
    DatasetPreviewRequest,
    ObjectDatasetCreate,
    ObjectDatasetUpdate,
    ObjectInspect,
    ObjectSelection,
    SqlDatasetCreate,
    SqlInspect,
    SqlSelection,
)
from app.modules.viz.contracts.datasets import DatasetDefinition
from app.modules.viz.contracts.parameters import Parameter, ParameterInput
from app.sql_frontend.binding.models import BoundColumn, BoundTable, TableName
from app.sql_frontend.binding.starrocks import source_projection
from app.sql_frontend.errors import SemanticError


def schema_result(columns):
    return QueryResult(
        columns=["ordinal", "name", "physical_type", "nullable"],
        rows=[
            [str(index), c.name, c.data_type, "YES" if c.nullable else "NO"]
            for index, c in enumerate(columns, 1)
        ],
    )


@pytest.fixture
async def preview_setup(setup, inspection_request, monkeypatch):
    inspected = await setup.service.inspect(setup.user, inspection_request)
    document = DatasetDefinition(**inspected.model_dump(), title="Orders")
    snapshot = {
        "revision": 1,
        "metadata_revision": 1,
        "grants_revision": 1,
        "publication_id": None,
        "definition": document,
    }
    repository = SimpleNamespace(require_kind=AsyncMock(), read=AsyncMock(return_value=snapshot))
    in_transaction = False

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        nonlocal in_transaction
        assert not in_transaction
        assert kwargs["read_only"]
        in_transaction = True
        try:
            yield repository
        finally:
            in_transaction = False

    async def execute(**kwargs):
        assert not in_transaction
        columns = [field.source_column for field in document.fields]
        if "row_sink" not in kwargs:
            return schema_result(tuple(
                BoundColumn(f.source_column, f.physical_type, index, f.nullable)
                for index, f in enumerate(document.fields, 1)
            ))
        sink = kwargs["row_sink"]
        await sink.begin(columns, ())
        await sink.rows(
            [
                [
                    9223372036854775807,
                    Decimal("99999999999999999999999999.123456789012"),
                    datetime(2024, 2, 29),
                ],
                [2, None, None],
                [3, Decimal("0"), None],
            ]
        )
        return QueryResult(columns=columns, row_count=3, streamed=True)

    monkeypatch.setattr(module, "document_transaction", transaction)
    setup.execute.reset_mock()
    setup.provider.get_source_schema.reset_mock()
    setup.execute.side_effect = execute
    return SimpleNamespace(
        **vars(setup),
        document=document,
        snapshot=snapshot,
        repository=repository,
        execute_rows=execute,
        request=DatasetPreviewRequest(revision=1, limit=2, offset=4),
    )


async def test_preview_preserves_exact_values_and_queries_only_as_caller(preview_setup):
    case = preview_setup
    result = await case.service.preview(case.user, case.document.dataset_id, case.request)
    assert result.rows[0][0].value == "9223372036854775807"
    assert result.rows[0][1].value == "99999999999999999999999999.123456789012"
    assert result.rows[0][2].value == "2024-02-29T00:00:00+07:00"
    assert result.rows[1][1:] == (None, None)
    assert result.has_more and result.next_offset == 6
    assert "total" not in result.model_dump() and "sql" not in result.model_dump()
    query = case.execute.call_args_list[1].kwargs
    assert query["sql"].endswith("LIMIT 3 OFFSET 4")
    assert (
        query["username"],
        query["role"],
        query["session_id"],
        query["security_context_version"],
    ) == (
        "alice",
        "ANALYST",
        "alice-login",
        5,
    )
    assert case.repository.read.await_count == 2
    assert all(call.kwargs == {"draft": True, "revision": 1}
               for call in case.repository.read.await_args_list)
    assert case.provider.get_source_schema.await_count == 4


async def test_preview_denied_document_never_reaches_data_engine(preview_setup):
    case = preview_setup
    case.repository.read.side_effect = MetadataPermissionDenied()
    with pytest.raises(MetadataPermissionDenied):
        await case.service.preview(case.user, case.document.dataset_id, case.request)
    case.execute.assert_not_awaited()
    case.provider.get_source_schema.assert_not_awaited()


async def test_preview_stale_revision_never_reaches_data_engine(preview_setup):
    case = preview_setup
    with pytest.raises(NovaException, match="revision changed"):
        await case.service.preview(
            case.user, case.document.dataset_id, case.request.model_copy(update={"revision": 2})
        )
    case.execute.assert_not_awaited()


async def test_publication_preview_does_not_request_an_unpublished_revision(preview_setup):
    case = preview_setup
    await case.service.preview(case.user, case.document.dataset_id,
                               case.request.model_copy(update={"draft": False}))
    assert case.repository.read.await_count == 2
    assert all(call.kwargs == {"draft": False, "revision": None}
               for call in case.repository.read.await_args_list)


@pytest.mark.parametrize("when", ["before", "after"])
async def test_preview_refuses_reused_source_identity(preview_setup, source, when):
    case = preview_setup
    other = replace(source, physical_id="99999")
    case.provider.get_source_schema.side_effect = (
        [other, other] if when == "before" else [source, source, other, other]
    )
    with pytest.raises(NovaException, match="SCHEMA_STALE"):
        await case.service.preview(case.user, case.document.dataset_id, case.request)


@pytest.mark.parametrize("change", ["metadata_revision", "grants_revision", "publication_id"])
async def test_preview_discards_rows_when_metadata_changes(preview_setup, change):
    case = preview_setup
    case.repository.read.side_effect = [case.snapshot, {**case.snapshot, change: 2}]
    with pytest.raises(NovaException, match="access or revision changed"):
        await case.service.preview(case.user, case.document.dataset_id, case.request)


async def test_preview_role_switch_while_reading_discards_rows(preview_setup):
    case = preview_setup

    async def execute(**kwargs):
        result = await case.execute_rows(**kwargs)
        if "row_sink" in kwargs:
            case.stored["security_context_version"] += 1
        return result

    case.execute.side_effect = execute
    with pytest.raises(MetadataPermissionDenied):
        await case.service.preview(case.user, case.document.dataset_id, case.request)


async def test_preview_engine_failure_is_redacted(preview_setup):
    case = preview_setup
    case.execute.side_effect = StarRocksError("private-engine-marker")
    with pytest.raises(NovaException) as error:
        await case.service.preview(case.user, case.document.dataset_id, case.request)
    assert error.value.status_code == 403
    assert "private-engine-marker" not in str(error.value)


@pytest.mark.parametrize("failure", [
    StarRocksError("Connection error: (2003, \"Can't connect\")"),
    StarRocksError("SQL error: (1203) too many connections", engine_code=1203),
    StarRocksError("SQL error: (1040) too many connections", engine_code=1040),
])
async def test_an_unreachable_engine_is_unavailable_not_a_refusal(preview_setup, failure):
    """An engine that is down or full answers 503, so nobody is told they lack access."""
    case = preview_setup
    case.execute.side_effect = failure
    with pytest.raises(NovaException) as error:
        await case.service.preview(case.user, case.document.dataset_id, case.request)
    assert error.value.status_code == 503
    assert "not responding" in error.value.message
    assert "connect" not in error.value.message.lower()


async def test_an_engine_refusal_stays_a_refusal(preview_setup):
    case = preview_setup
    case.execute.side_effect = StarRocksError("SQL error: (1142) denied", engine_code=1142)
    with pytest.raises(NovaException) as error:
        await case.service.preview(case.user, case.document.dataset_id, case.request)
    assert error.value.status_code == 403


async def test_preview_deadline_cancels_the_stream(preview_setup, monkeypatch):
    case = preview_setup
    cancelled = asyncio.Event()

    async def execute(**kwargs):
        if "row_sink" not in kwargs:
            return await case.execute_rows(**kwargs)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    case.execute.side_effect = execute
    monkeypatch.setattr(module, "SOURCE_TIMEOUT_SECONDS", 0.02)
    with pytest.raises(NovaException) as error:
        await case.service.preview(case.user, case.document.dataset_id, case.request)
    assert error.value.status_code == 504 and cancelled.is_set()


async def test_preview_capacity_refusal_precedes_metadata_and_engine_io(preview_setup, monkeypatch):
    case = preview_setup
    admission = QueryAdmission()
    monkeypatch.setattr(module, "query_admission", admission)
    audit = AsyncMock()
    monkeypatch.setattr(module, "audit_refusal", audit)
    async with AsyncExitStack() as stack:
        for _ in range(4):
            await stack.enter_async_context(admission.slot("viz", source_limit=4))
        with pytest.raises(NovaException) as error:
            await case.service.preview(case.user, case.document.dataset_id, case.request)
        assert error.value.status_code == 429
        case.repository.read.assert_not_awaited()
        case.execute.assert_not_awaited()
    assert admission.active == 0
    audit.assert_awaited_once()


@pytest.fixture
def source():
    return BoundTable(
        TableName("orders", database="analytics"),
        "BASE TABLE",
        columns=(
            BoundColumn("id", "bigint(20)", 1, False),
            BoundColumn("amount", "decimal(38,12)", 2, True),
            BoundColumn("created_at", "datetime", 3, True),
        ),
        physical_id="10081",
        schema_version="a" * 64,
    )


@pytest.fixture
def inspection_request():
    return ObjectInspect(
        dataset_id=uuid4(),
        source=ObjectSelection(
            kind="table",
            database="analytics",
            object_name="orders",
        ),
    )


@pytest.fixture
def setup(monkeypatch, source):
    user = {
        "username": "alice",
        "active_role": "ANALYST",
        "assigned_roles": ["ANALYST"],
        "session_id": "alice-login",
        "security_context_version": 5,
        "encrypted_password": "private-envelope",
    }
    stored = {**user}
    monkeypatch.setattr(document_service.session_store, "get", AsyncMock(return_value=stored))
    monkeypatch.setattr(document_service, "write_audit_log", AsyncMock())
    provider = SimpleNamespace(get_source_schema=AsyncMock(return_value=source))
    contexts = []

    def factory(repository, context, password):
        contexts.append(context)
        return provider

    monkeypatch.setattr(module, "StarRocksCatalogProvider", factory)
    execute = AsyncMock(return_value=schema_result(source.columns))
    monkeypatch.setattr(module.query_service, "execute", execute)
    monkeypatch.setattr(module, "configured_timezone", lambda: "Asia/Jakarta")
    return SimpleNamespace(
        user=user,
        stored=stored,
        provider=provider,
        execute=execute,
        contexts=contexts,
        service=module.NativeDatasetService(),
    )


async def test_empty_source_has_catalog_owned_exact_schema_and_stable_ids(
    setup, inspection_request
):
    result = await setup.service.inspect(setup.user, inspection_request)
    assert result.source.physical_id == "10081"
    assert len(result.schema_fingerprint) == 64
    assert result.schema_fingerprint != "a" * 64
    assert [f.logical_type for f in result.fields] == ["integer", "decimal", "timestamp"]
    assert [f.nullable for f in result.fields] == [False, True, True]
    assert result.fields[1].physical_type == "decimal(38,12)"
    assert result.fields[2].timezone == "Asia/Jakarta"
    assert result == await setup.service.inspect(setup.user, inspection_request)
    assert "private-envelope" not in result.model_dump_json()
    assert "private-envelope" not in repr(setup.contexts[0])
    call = setup.execute.call_args.kwargs
    assert call["sql"] == (
        "EXPLAIN SCHEMA SELECT `id`,`amount`,`created_at` "
        "FROM `default_catalog`.`analytics`.`orders` LIMIT 0"
    )
    assert call["max_rows"] == 512
    assert (
        call["username"],
        call["role"],
        call["session_id"],
        call["security_context_version"],
    ) == (
        "alice",
        "ANALYST",
        "alice-login",
        5,
    )


async def test_selection_preserves_requested_order_and_engine_spelling(
    setup, inspection_request, source
):
    selection = inspection_request.source.model_copy(update={"columns": ("AMOUNT", "id")})
    setup.execute.return_value = schema_result((source.columns[1], source.columns[0]))
    result = await setup.service.inspect(
        setup.user, inspection_request.model_copy(update={"source": selection})
    )
    assert [f.source_column for f in result.fields] == ["amount", "id"]


@pytest.mark.parametrize(
    "data_type",
    ["bigint", "decimal(38,12)", "float", "double", "varchar(100)", "char(2)",
     "boolean", "date", "datetime"],
)
async def test_supported_source_types_need_no_sample_rows(
    setup, inspection_request, source, data_type
):
    table = replace(source, columns=(BoundColumn("value", data_type, 1, True),))
    setup.provider.get_source_schema.return_value = table
    setup.execute.return_value = schema_result(table.columns)
    result = await setup.service.inspect(setup.user, inspection_request)
    assert result.fields[0].physical_type == data_type


async def test_analyzer_distinguishes_boolean_from_catalog_tinyint(
    setup, inspection_request, source
):
    catalog = BoundColumn("flag", "tinyint(1)", 1, True)
    setup.provider.get_source_schema.return_value = replace(source, columns=(catalog,))
    setup.execute.return_value = schema_result((replace(catalog, data_type="BOOLEAN"),))
    boolean = await setup.service.inspect(setup.user, inspection_request)
    assert boolean.fields[0].logical_type == "boolean"
    assert boolean.fields[0].physical_type == "BOOLEAN"
    setup.execute.return_value = schema_result((replace(catalog, data_type="TINYINT"),))
    integer = await setup.service.inspect(setup.user, inspection_request)
    assert integer.fields[0].logical_type == "integer"
    assert integer.schema_fingerprint != boolean.schema_fingerprint
    assert integer.fields[0].field_id == boolean.fields[0].field_id


@pytest.mark.parametrize(
    "data_type", ["largeint", "array<int>", "map<int,int>", "json", "hll", "unknown"]
)
async def test_unrepresentable_types_fail_before_query_instead_of_guessing(
    setup, inspection_request, source, data_type
):
    setup.provider.get_source_schema.return_value = replace(
        source,
        columns=(BoundColumn("value", data_type, 1, True),),
    )
    with pytest.raises(NovaException) as error:
        await setup.service.inspect(setup.user, inspection_request)
    assert error.value.status_code == 422
    setup.execute.assert_not_awaited()


@pytest.mark.parametrize("change", ["physical_id", "schema_version"])
async def test_replacement_or_schema_change_during_query_is_stale(
    setup, inspection_request, source, change
):
    setup.provider.get_source_schema.side_effect = [
        source,
        replace(source, **{change: "different"}),
    ]
    with pytest.raises(NovaException, match="SCHEMA_STALE") as error:
        await setup.service.inspect(setup.user, inspection_request)
    assert error.value.status_code == 409


async def test_catalog_reflection_does_not_substitute_for_select_permission(
    setup, inspection_request
):
    setup.execute.side_effect = StarRocksError("private-engine-error-marker")
    with pytest.raises(NovaException) as error:
        await setup.service.inspect(setup.user, inspection_request)
    assert error.value.status_code == 403
    assert "private-engine-error-marker" not in str(error.value)
    assert document_service.write_audit_log.call_args.kwargs["decision"] == "DENY"


@pytest.mark.parametrize(
    "result",
    [
        QueryResult(columns=["other"]),
        QueryResult(columns=["id", "amount", "created_at"], rows=[[1]]),
        QueryResult(error="private-engine-marker"),
    ],
)
async def test_projection_protocol_mismatch_does_not_infer_from_rows(
    setup, inspection_request, result
):
    setup.execute.return_value = result
    with pytest.raises(NovaException) as error:
        await setup.service.inspect(setup.user, inspection_request)
    assert error.value.status_code == 422
    assert "private-engine-marker" not in str(error.value)


async def test_role_change_during_inspection_discards_the_schema(setup, inspection_request, source):
    async def execute(**kwargs):
        setup.stored["security_context_version"] += 1
        return schema_result(source.columns)

    setup.execute.side_effect = execute
    with pytest.raises(MetadataPermissionDenied):
        await setup.service.inspect(setup.user, inspection_request)


async def test_expired_session_does_not_read_catalog(setup, inspection_request):
    setup.stored["assigned_roles"] = []
    with pytest.raises(MetadataPermissionDenied):
        await setup.service.inspect(setup.user, inspection_request)
    setup.provider.get_source_schema.assert_not_awaited()


async def test_catalog_refusal_is_audited_without_echoing_object_details(setup, inspection_request):
    setup.provider.get_source_schema.side_effect = SemanticError("private-catalog-marker")
    with pytest.raises(NovaException) as error:
        await setup.service.inspect(setup.user, inspection_request)
    assert error.value.status_code == 403
    assert "private-catalog-marker" not in str(error.value)
    assert document_service.write_audit_log.call_args.kwargs["decision"] == "DENY"
    setup.execute.assert_not_awaited()


async def test_inspection_deadline_cancels_dependency(setup, inspection_request, monkeypatch):
    cancelled = asyncio.Event()

    async def blocked(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(module, "SOURCE_TIMEOUT_SECONDS", 0.01)
    setup.provider.get_source_schema.side_effect = blocked
    with pytest.raises(NovaException) as error:
        await setup.service.inspect(setup.user, inspection_request)
    assert error.value.status_code == 504
    assert cancelled.is_set()


async def test_create_commits_only_server_derived_schema_after_external_io(
    setup, inspection_request, monkeypatch
):
    inspected = await setup.service.inspect(setup.user, inspection_request)
    setup.provider.get_source_schema.reset_mock()
    setup.execute.reset_mock()
    workspace = AsyncMock(return_value=SimpleNamespace(id="personal:alice"))
    monkeypatch.setattr(module, "document_workspace", workspace)
    repository = SimpleNamespace(create=AsyncMock(return_value={"receipt": "committed"}),
                                 dataset_intent_receipt=AsyncMock(return_value=None))

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        assert setup.provider.get_source_schema.await_count == (0 if kwargs.get("read_only") else 2)
        assert setup.execute.await_count == (0 if kwargs.get("read_only") else 1)
        yield repository

    monkeypatch.setattr(module, "document_transaction", transaction)
    body = ObjectDatasetCreate(
        **inspection_request.model_dump(),
        workspace_id="personal",
        title="Orders",
        expected_physical_id="10081",
        expected_schema_fingerprint=inspected.schema_fingerprint,
    )
    key = uuid4()
    result = await setup.service.create(setup.user, body, operation_id=key)
    assert result == {"receipt": "committed"}
    args = repository.create.call_args
    assert args.args[0] == "personal:alice"
    assert args.args[1].source.physical_id == "10081"
    assert args.args[1].fields[1].physical_type == "decimal(38,12)"
    assert args.kwargs["operation_id"] == key


@pytest.mark.parametrize(
    "field,value",
    [("expected_physical_id", "old-object"), ("expected_schema_fingerprint", "b" * 64)],
)
async def test_stale_inspection_cannot_open_metadata_write_transaction(
    setup, inspection_request, monkeypatch, field, value
):
    inspected = await setup.service.inspect(setup.user, inspection_request)
    monkeypatch.setattr(
        module, "document_workspace", AsyncMock(return_value=SimpleNamespace(id="p"))
    )
    repository = SimpleNamespace(dataset_intent_receipt=AsyncMock(return_value=None))

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        assert kwargs.get("read_only") is True
        yield repository
    monkeypatch.setattr(module, "document_transaction", transaction)
    values = {
        **inspection_request.model_dump(),
        "workspace_id": "personal",
        "title": "Orders",
        "expected_physical_id": "10081",
        "expected_schema_fingerprint": inspected.schema_fingerprint,
        field: value,
    }
    with pytest.raises(NovaException, match="SCHEMA_STALE"):
        await setup.service.create(setup.user, ObjectDatasetCreate(**values), operation_id=uuid4())
    assert repository.dataset_intent_receipt.await_count == 2


@pytest.fixture
async def update_setup(setup, inspection_request, monkeypatch):
    inspected = await setup.service.inspect(setup.user, inspection_request)
    current = DatasetDefinition(**inspected.model_dump(), title="Old title")
    repository = SimpleNamespace(
        dataset_intent_receipt=AsyncMock(return_value=None),
        require_kind=AsyncMock(),
        read=AsyncMock(return_value={"definition": current, "revision": 1}),
        replace=AsyncMock(return_value={"receipt": "committed"}),
    )
    in_transaction = False
    transactions = []

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        nonlocal in_transaction
        assert not in_transaction
        transactions.append(kwargs)
        in_transaction = True
        try:
            yield repository
        finally:
            in_transaction = False

    async def execute(**kwargs):
        assert not in_transaction
        return setup.execute.return_value

    monkeypatch.setattr(module, "document_transaction", transaction)
    setup.execute.reset_mock()
    setup.provider.get_source_schema.reset_mock()
    setup.execute.side_effect = execute
    request = ObjectDatasetUpdate(
        **inspection_request.model_dump(), title="New title",
        expected_physical_id=inspected.source.physical_id,
        expected_schema_fingerprint=inspected.schema_fingerprint,
    )
    return SimpleNamespace(
        **vars(setup), request=request, current=current, repository=repository,
        transactions=transactions,
    )


async def test_update_reinspects_before_revision_cas_without_holding_pg(update_setup):
    case = update_setup
    key = uuid4()
    result = await case.service.update(
        case.user, case.request, operation_id=key, expected_revision=1
    )
    assert result == {"receipt": "committed"}
    assert [scope.get("read_only", False) for scope in case.transactions] == [True, True, False]
    definition = case.repository.replace.call_args.args[0]
    assert definition.dataset_id == case.current.dataset_id
    assert definition.title == "New title" and definition.fields == case.current.fields
    assert case.current.title == "Old title"
    assert case.repository.replace.call_args.kwargs == {
        "operation_id": key, "expected_revision": 1, "intent": case.request,
    }


async def test_update_replay_does_not_reopen_changed_source(update_setup):
    case = update_setup
    receipt = {"new_revision": 2}
    case.repository.dataset_intent_receipt.return_value = receipt
    assert await case.service.update(
        case.user, case.request, operation_id=uuid4(), expected_revision=1,
    ) == receipt
    case.repository.read.assert_not_awaited()
    case.repository.replace.assert_not_awaited()
    case.provider.get_source_schema.assert_not_awaited()
    case.execute.assert_not_awaited()
    assert [scope.get("read_only") for scope in case.transactions] == [True]


async def test_update_receipt_conflict_prevents_source_inspection(update_setup):
    case = update_setup
    case.repository.dataset_intent_receipt.side_effect = MetadataConflict()
    with pytest.raises(MetadataConflict):
        await case.service.update(
            case.user, case.request, operation_id=uuid4(), expected_revision=1,
        )
    case.repository.read.assert_not_awaited()
    case.repository.replace.assert_not_awaited()
    case.provider.get_source_schema.assert_not_awaited()
    case.execute.assert_not_awaited()


async def test_update_source_error_rechecks_concurrent_receipt(update_setup, source):
    case = update_setup
    receipt = {"new_revision": 2}
    case.repository.dataset_intent_receipt.side_effect = [None, receipt]
    case.provider.get_source_schema.return_value = replace(source, physical_id="replaced")
    assert await case.service.update(
        case.user, case.request, operation_id=uuid4(), expected_revision=1,
    ) == receipt
    case.repository.replace.assert_not_awaited()
    assert case.repository.dataset_intent_receipt.await_count == 2
    assert all(scope["read_only"] for scope in case.transactions)


async def test_update_checks_document_permission_before_source_io(update_setup):
    case = update_setup
    case.repository.read.side_effect = MetadataPermissionDenied()
    with pytest.raises(MetadataPermissionDenied):
        await case.service.update(
            case.user, case.request, operation_id=uuid4(), expected_revision=1
        )
    case.execute.assert_not_awaited()
    case.provider.get_source_schema.assert_not_awaited()
    case.repository.replace.assert_not_awaited()


async def test_update_refuses_a_source_that_changed_after_inspection(update_setup, source):
    case = update_setup
    case.provider.get_source_schema.return_value = replace(source, physical_id="999999")
    with pytest.raises(NovaException, match="SCHEMA_STALE"):
        await case.service.update(
            case.user, case.request, operation_id=uuid4(), expected_revision=1
        )
    case.repository.replace.assert_not_awaited()


async def test_update_keeps_concurrent_edit_conflicts(update_setup):
    case = update_setup
    case.repository.replace.side_effect = MetadataConflict()
    with pytest.raises(MetadataConflict):
        await case.service.update(
            case.user, case.request, operation_id=uuid4(), expected_revision=1
        )


async def test_source_update_cannot_silently_remove_semantic_bindings(update_setup):
    case = update_setup
    document = case.current.model_copy(update={"grain": (case.current.fields[0].field_id,)})
    case.repository.read.return_value = {"definition": document, "revision": 1}
    with pytest.raises(NovaException, match="semantic bindings"):
        await case.service.update(
            case.user, case.request, operation_id=uuid4(), expected_revision=1
        )
    case.execute.assert_not_awaited()
    case.repository.replace.assert_not_awaited()


def test_projection_quotes_catalog_bound_names_as_identifiers(source):
    table = replace(
        source,
        name=TableName("x`; DROP TABLE x;--", database="my db"),
        columns=(BoundColumn("x`y", "bigint", 1, False),),
    )
    sql = source_projection(table, ("x`y",), limit=0)
    assert sql == "SELECT `x``y` FROM `default_catalog`.`my db`.`x``; DROP TABLE x;--` LIMIT 0"


@pytest.mark.parametrize(
    "columns,limit",
    [
        ((), 1),
        (("unknown",), 1),
        (("id", "id"), 1),
        (("id",), -1),
        (("id",), 1001),
        (("id",), True),
    ],
)
def test_projection_rejects_unbound_columns_or_limits(source, columns, limit):
    with pytest.raises(SemanticError):
        source_projection(source, columns, limit=limit)


@pytest.mark.parametrize(
    "changes",
    [{"physical_id": "spoof"}, {"owner": "root"}, {"columns": []}, {"columns": ["id", "ID"]}],
)
def test_source_selection_rejects_authority_fields_and_ambiguous_projection(changes):
    with pytest.raises(ValidationError):
        ObjectSelection(kind="table", database="analytics", object_name="orders", **changes)


@pytest.fixture
def sql_request(setup):
    setup.execute.return_value = QueryResult(
        columns=["ordinal", "name", "physical_type", "nullable"],
        rows=[["1", "total", "decimal(38,12)", "YES"]],
    )
    return SqlInspect(
        dataset_id=uuid4(),
        source=SqlSelection(
            database="analytics",
            sql="SELECT CAST(sum(amount) AS DECIMAL(38,12)) AS total FROM orders",
        ),
    )


async def test_sql_inspection_binds_sources_and_uses_authorized_engine_schema(setup, sql_request):
    result = await setup.service.inspect(setup.user, sql_request)
    assert result.source.kind == "sql"
    assert result.source.dependencies[0].physical_id == "10081"
    assert result.source.dependencies[0].object_name == "orders"
    assert len(result.source.ast_digest) == 64
    assert len(result.schema_fingerprint) == 64
    assert result.fields[0].name == "total"
    assert result.fields[0].logical_type == "decimal"
    assert result.fields[0].nullable is True
    call = setup.execute.call_args.kwargs
    assert call["sql"] == "EXPLAIN SCHEMA " + sql_request.source.sql
    assert call["username"] == "alice" and call["role"] == "ANALYST"
    assert call["session_id"] == "alice-login" and call["security_context_version"] == 5
    assert call["max_rows"] == 512
    assert setup.provider.get_source_schema.await_count == 2
    repeated = await setup.service.inspect(setup.user, sql_request)
    assert repeated == result


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "SELECT * FROM orders; SELECT 1",
        "SELECT * FROM",
        "SELECT :value AS total FROM orders",
        "SELECT * FROM foreign_catalog.db.t",
        "SELECT 1 AS total",
        "SELECT sleep(1) AS total FROM orders",
        "INSERT INTO orders SELECT * FROM orders",
        "SELECT * FROM orders; DELETE FROM orders",
        "WITH x AS (SELECT sleep(5) AS total FROM orders) SELECT total FROM x",
        "UPDATE orders SET total = 0",
        "SELECT total FROM orders INTO OUTFILE 's3://bucket/x'",
        "SELECT total FROM orders /* ; DROP TABLE orders */ ; DROP TABLE orders",
    ],
)
async def test_invalid_sql_dataset_never_reaches_catalog_or_engine(setup, sql_request, sql):
    request = sql_request.model_copy(update={"source": SqlSelection(database="analytics", sql=sql)})
    with pytest.raises(NovaException) as error:
        await setup.service.inspect(setup.user, request)
    assert error.value.status_code == 422
    setup.execute.assert_not_awaited()
    setup.provider.get_source_schema.assert_not_awaited()


async def test_sql_replaced_source_is_refused_after_engine_inspection(setup, sql_request, source):
    setup.provider.get_source_schema.side_effect = [source, replace(source, physical_id="new")]
    with pytest.raises(NovaException, match="SCHEMA_STALE"):
        await setup.service.inspect(setup.user, sql_request)


async def test_sql_schema_change_alters_fingerprint(setup, sql_request):
    first = await setup.service.inspect(setup.user, sql_request)
    setup.execute.return_value.rows[0][2] = "bigint(20)"
    second = await setup.service.inspect(setup.user, sql_request)
    assert first.schema_fingerprint != second.schema_fingerprint
    assert first.fields[0].field_id == second.fields[0].field_id


async def test_sql_comments_and_terminator_do_not_change_field_identity(setup, sql_request):
    first = await setup.service.inspect(setup.user, sql_request)
    request = sql_request.model_copy(
        update={
            "source": sql_request.source.model_copy(
                update={
                    "sql": "/* note */ "
                    + sql_request.source.sql.replace("SELECT ", "SELECT  ")
                    + "; -- end",
                }
            )
        }
    )
    second = await setup.service.inspect(setup.user, request)
    assert first.source.ast_digest == second.source.ast_digest
    assert first.schema_fingerprint == second.schema_fingerprint
    assert first.fields == second.fields


async def test_sql_create_persists_only_verified_definition_after_external_reads(
    setup,
    sql_request,
    monkeypatch,
):
    inspected = await setup.service.inspect(setup.user, sql_request)
    monkeypatch.setattr(
        module, "document_workspace", AsyncMock(return_value=SimpleNamespace(id="p"))
    )
    repository = SimpleNamespace(create=AsyncMock(return_value={"committed": True}),
                                 dataset_intent_receipt=AsyncMock(return_value=None))

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        assert setup.execute.await_count == (1 if kwargs.get("read_only") else 2)
        assert setup.provider.get_source_schema.await_count == (2 if kwargs.get("read_only") else 4)
        yield repository

    monkeypatch.setattr(module, "document_transaction", transaction)
    body = SqlDatasetCreate(
        **sql_request.model_dump(),
        workspace_id="personal",
        title="Revenue",
        expected_schema_fingerprint=inspected.schema_fingerprint,
    )
    assert await setup.service.create(setup.user, body, operation_id=uuid4()) == {"committed": True}
    document = repository.create.call_args.args[1]
    assert document.source == inspected.source and document.fields == inspected.fields
    assert "rows" not in document.model_dump()


async def test_sql_create_cannot_persist_stale_preview(setup, sql_request, monkeypatch):
    monkeypatch.setattr(
        module, "document_workspace", AsyncMock(return_value=SimpleNamespace(id="p"))
    )
    repository = SimpleNamespace(dataset_intent_receipt=AsyncMock(return_value=None))

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        assert kwargs.get("read_only") is True
        yield repository
    monkeypatch.setattr(module, "document_transaction", transaction)
    body = SqlDatasetCreate(
        **sql_request.model_dump(),
        workspace_id="personal",
        title="Revenue",
        expected_schema_fingerprint="0" * 64,
    )
    with pytest.raises(NovaException, match="SCHEMA_STALE"):
        await setup.service.create(setup.user, body, operation_id=uuid4())
    assert repository.dataset_intent_receipt.await_count == 2


@pytest.fixture
def parameter_request(sql_request):
    return SqlInspect(
        dataset_id=sql_request.dataset_id,
        source=SqlSelection(
            database="analytics", sql="SELECT :amount AS total FROM orders WHERE id IN (:ids)",
        ),
        parameters=(
            Parameter(parameter_id="amount", type="decimal", precision=38, scale=12),
            Parameter(parameter_id="ids", type="integer", multiple=True),
        ),
    )


async def test_parameter_inspection_requires_only_declarations_and_persists_their_meaning(
    setup, parameter_request,
):
    request = parameter_request
    result = await setup.service.inspect(setup.user, request)
    assert result.parameters == request.parameters
    assert setup.execute.call_args.kwargs["sql"] == (
        "EXPLAIN SCHEMA SELECT CAST(NULL AS DECIMAL(38,12)) AS total "
        "FROM orders WHERE id IN (CAST(NULL AS BIGINT))"
    )
    saved = await setup.service._definition(setup.user, SqlDatasetCreate(
        **request.model_dump(), title="Parameterized orders", workspace_id="personal",
        expected_schema_fingerprint=result.schema_fingerprint,
    ))
    assert saved.parameters == request.parameters
    assert setup.service._selection(saved) == request
    changed = request.model_copy(update={"parameters": (
        request.parameters[0].model_copy(update={"scale": 10}), request.parameters[1],
    )})
    observed = await setup.service.inspect(setup.user, changed)
    assert observed.schema_fingerprint != result.schema_fingerprint
    assert observed.fields == result.fields


async def test_parameter_preview_keeps_values_private_and_uses_existing_stream_and_identity(
    setup, parameter_request, monkeypatch,
):
    inspected = await setup.service.inspect(setup.user, parameter_request)
    document = DatasetDefinition(**inspected.model_dump(), title="Orders")
    snapshot = {"revision": 1, "metadata_revision": 1, "grants_revision": 1,
                "publication_id": None, "definition": document}
    repository = SimpleNamespace(require_kind=AsyncMock(), read=AsyncMock(return_value=snapshot))

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        assert kwargs["read_only"]
        yield repository

    schema = setup.execute.return_value
    exact = Decimal("12345678901234567890123456.123456789012")

    async def execute(**kwargs):
        if "row_sink" not in kwargs:
            assert "CAST(NULL AS" in kwargs["sql"]
            return schema
        assert ":amount" in kwargs["sql"] and str(exact) not in kwargs["sql"]
        assert kwargs["parameters"].values == (("amount", exact), ("ids", (1, 2)))
        assert (kwargs["username"], kwargs["role"], kwargs["session_id"]) == (
            "alice", "ANALYST", "alice-login",
        )
        assert kwargs["security_context_version"] == 5 and kwargs["max_rows"] == 3
        await kwargs["row_sink"].begin(["total"], ())
        await kwargs["row_sink"].rows([[exact]])
        return QueryResult(columns=["total"], row_count=1, streamed=True)

    setup.execute.side_effect = execute
    setup.execute.reset_mock()
    monkeypatch.setattr(module, "document_transaction", transaction)
    request = DatasetPreviewRequest(revision=1, limit=2, parameters=(
        ParameterInput(parameter_id="amount", values=({"type": "decimal", "value": str(exact)},)),
        ParameterInput(parameter_id="ids", values=(
            {"type": "integer", "value": "1"}, {"type": "integer", "value": "2"},
        )),
    ))
    result = await setup.service.preview(setup.user, document.dataset_id, request)
    assert result.rows[0][0].value == str(exact)
    assert setup.execute.await_count == 3 and repository.read.await_count == 2
    assert document.parameters[0].defaults == () and "parameters" not in result.model_dump()
    setup.execute.reset_mock()
    with pytest.raises(NovaException) as error:
        await setup.service.preview(
            setup.user, document.dataset_id, DatasetPreviewRequest(revision=1),
        )
    assert error.value.status_code == 422
    setup.execute.assert_not_awaited()


# A refusal before admission (no valid context) or at session revalidation.
REFUSED = (NovaException, HTTPException)


@pytest.mark.parametrize("claim", [
    # A service or metadata login name with no Nova sign-in session.
    {"username": "nova_meta_web", "session_id": "nova_meta_web", "active_role": "NOVA_SERVICE"},
    {"username": "root", "session_id": "root-login", "active_role": "ACCOUNTADMIN"},
    # A forged scheduled-task session with no impersonated engine connection.
    {"username": "nova_task", "session_id": "task:forged", "active_role": "PUBLIC"},
    # No session at all.
    {"username": "alice", "session_id": "", "active_role": "ANALYST"},
])
async def test_service_or_metadata_identity_cannot_query_dataset_rows(
    preview_setup, monkeypatch, claim,
):
    """NDV-ACL-024: only a live caller session reaches the data engine."""
    case = preview_setup
    monkeypatch.setattr(document_service.session_store, "get", AsyncMock(return_value=None))
    user = {**case.user, **claim, "assigned_roles": [claim["active_role"]]}
    with pytest.raises(REFUSED):
        await case.service.preview(user, case.document.dataset_id, case.request)
    case.execute.assert_not_awaited()


@pytest.mark.parametrize("claim", [
    {"username": "root", "active_role": "ACCOUNTADMIN", "assigned_roles": ["ACCOUNTADMIN"]},
    {"username": "nova_meta_web", "active_role": "ANALYST", "assigned_roles": ["ANALYST"]},
    {"username": "alice", "active_role": "ANALYST", "assigned_roles": ["ANALYST"],
     "security_context_version": 4},
])
async def test_live_session_cannot_be_borrowed_by_another_principal(preview_setup, claim):
    """NDV-ACL-024: a live session id with another account or context is refused."""
    case = preview_setup
    user = {**case.user, **claim}
    with pytest.raises(REFUSED):
        await case.service.preview(user, case.document.dataset_id, case.request)
    case.execute.assert_not_awaited()


async def test_task_connection_for_one_account_does_not_serve_another(preview_setup, monkeypatch):
    """NDV-ACL-024: an impersonated task connection only counts for its own account."""
    from app.modules.query.service import delegated_connection

    case = preview_setup
    monkeypatch.setattr(document_service.session_store, "get", AsyncMock(return_value=None))
    user = {**case.user, "username": "alice", "session_id": "task:run-1",
            "active_role": "ANALYST", "assigned_roles": ["ANALYST"]}
    with delegated_connection("nova_task_reporting", object()), pytest.raises(REFUSED):
        await case.service.preview(user, case.document.dataset_id, case.request)
    case.execute.assert_not_awaited()
