"""Release QA: the governed dataset query path, stale sources, admission and redaction.

NDV-CACHE-007 (a source schema change blocks stale results), NDV-CACHE-011
(the query endpoint is admission bounded) and NDV-DATA-016 (secret-like
literals in source SQL never reach diagnostics, logs or audit rows).
"""

import logging
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import NovaException, StarRocksError
from app.modules.query.admission import QueryAdmission
from app.modules.query.repository import QueryResult
from app.modules.viz import dataset_service as module
from app.modules.viz import document_service
from app.modules.viz.contracts.authoring import SqlInspect, SqlSelection
from app.modules.viz.contracts.datasets import DatasetDefinition
from app.modules.viz.contracts.queries import DatasetQueryRequest
from app.sql_frontend.binding.models import BoundColumn
from tests.unit.test_native_viz_datasets import (  # noqa: F401 - pytest fixtures
    inspection_request,
    schema_result,
    setup,
    source,
)


@pytest.fixture
async def query_setup(setup, inspection_request, monkeypatch):  # noqa: F811
    inspected = await setup.service.inspect(setup.user, inspection_request)
    document = DatasetDefinition(**inspected.model_dump(), title="Orders")
    snapshot = {"revision": 1, "metadata_revision": 1, "grants_revision": 1,
                "publication_id": None, "definition": document}
    repository = SimpleNamespace(require_kind=AsyncMock(), read=AsyncMock(return_value=snapshot),
                                 revision=AsyncMock(return_value=document))

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        assert kwargs["read_only"]
        yield repository

    aggregates: list[dict] = []

    async def execute(**kwargs):
        if "row_sink" not in kwargs:
            return schema_result(tuple(
                BoundColumn(f.source_column, f.physical_type, index, f.nullable)
                for index, f in enumerate(document.fields, 1)))
        aggregates.append(kwargs)
        sink = kwargs["row_sink"]
        await sink.begin(["d0", "m0"], ())
        await sink.rows([[1, Decimal("10.50")], [2, Decimal("4.00")]])
        return QueryResult(columns=["d0", "m0"], row_count=2, streamed=True)

    monkeypatch.setattr(module, "document_transaction", transaction)
    setup.execute.reset_mock()
    setup.provider.get_source_schema.reset_mock()
    setup.execute.side_effect = execute
    fields = {f.source_column: f.field_id for f in document.fields}
    request = DatasetQueryRequest(
        revision=1, dimensions=[{"field_id": fields["id"]}],
        measures=[{"field_id": fields["amount"], "aggregation": "sum"}])
    return SimpleNamespace(**vars(setup), document=document, repository=repository,
                           aggregates=aggregates, request=request)


async def test_an_unchanged_source_answers_the_pinned_revision(query_setup):
    case = query_setup
    result = await case.service.query(case.user, case.document.dataset_id, case.request)
    assert [row[1].value for row in result.rows] == ["10.50", "4.00"]
    assert result.revision == 1 and len(case.aggregates) == 1
    # Source identity is checked before and after the aggregate runs.
    assert case.provider.get_source_schema.await_count == 4


# ── NDV-CACHE-007: a source schema revision change blocks stale results ──


@pytest.mark.parametrize("change", ["schema_version", "physical_id"])
async def test_a_source_changed_before_the_query_is_refused_without_running_it(
    query_setup, source, change,  # noqa: F811
):
    case = query_setup
    case.provider.get_source_schema.return_value = replace(source, **{change: "b" * 64})
    with pytest.raises(NovaException, match="SCHEMA_STALE") as error:
        await case.service.query(case.user, case.document.dataset_id, case.request)
    assert error.value.status_code == 409
    assert case.aggregates == []


async def test_a_column_type_change_on_the_same_source_is_stale(query_setup, source):  # noqa: F811
    case = query_setup
    widened = replace(source.columns[1], data_type="decimal(38,4)")

    async def execute(**kwargs):
        assert "row_sink" not in kwargs, "the aggregate must not run on a changed schema"
        return schema_result((source.columns[0], widened, source.columns[2]))

    case.execute.side_effect = execute
    with pytest.raises(NovaException, match="SCHEMA_STALE") as error:
        await case.service.query(case.user, case.document.dataset_id, case.request)
    assert error.value.status_code == 409


async def test_a_source_changed_while_the_query_ran_discards_its_rows(
    query_setup, source,  # noqa: F811
):
    case = query_setup
    changed = replace(source, schema_version="c" * 64)
    # Before: inspect reads the source twice; after the aggregate it has changed.
    case.provider.get_source_schema.side_effect = [source, source, changed, changed]
    with pytest.raises(NovaException, match="SCHEMA_STALE") as error:
        await case.service.query(case.user, case.document.dataset_id, case.request)
    assert error.value.status_code == 409
    assert len(case.aggregates) == 1  # it ran, but no row reached the caller


async def test_a_query_for_another_revision_never_reaches_the_engine(query_setup):
    case = query_setup
    with pytest.raises(NovaException) as error:
        await case.service.query(case.user, case.document.dataset_id,
                                 case.request.model_copy(update={"revision": 2}))
    assert error.value.status_code == 404
    case.execute.assert_not_awaited()
    case.provider.get_source_schema.assert_not_awaited()


# ── NDV-CACHE-011: admission on the query endpoint ───────────────────────


async def test_query_capacity_refusal_precedes_metadata_and_engine_io(query_setup, monkeypatch):
    case = query_setup
    admission = QueryAdmission()
    monkeypatch.setattr(module, "query_admission", admission)
    audit = AsyncMock()
    monkeypatch.setattr(module, "audit_refusal", audit)
    async with AsyncExitStack() as stack:
        for _ in range(4):
            await stack.enter_async_context(admission.slot("viz", source_limit=4))
        refusals = []
        for _ in range(5):
            with pytest.raises(NovaException) as error:
                await case.service.query(case.user, case.document.dataset_id, case.request)
            refusals.append(error.value.status_code)
        assert refusals == [429] * 5
        assert "retry shortly" in str(error.value)
        case.repository.read.assert_not_awaited()
        case.execute.assert_not_awaited()
        case.provider.get_source_schema.assert_not_awaited()
        assert admission.active == 4
    assert admission.active == 0
    assert audit.await_count == 5
    assert audit.await_args.kwargs["source"] == "viz"
    assert audit.await_args.kwargs["target"] == str(case.document.dataset_id)
    # Once capacity is released, the same request is answered.
    result = await case.service.query(case.user, case.document.dataset_id, case.request)
    assert len(result.rows) == 2 and admission.active == 0


# ── NDV-DATA-016: secret-like literals in source SQL ─────────────────────

SECRETS = ("hunter2-private-pw", "AKIAIOSFODNN7EXAMPLE", "sk-live-a1B2c3D4e5F6g7H8")
SECRET_SQL = (
    "SELECT CAST(sum(amount) AS DECIMAL(38,12)) AS total FROM orders "
    f"WHERE note <> 'password={SECRETS[0]}' AND note <> '{SECRETS[1]}' "
    f"AND note <> 'token {SECRETS[2]}'"
)


def _sql_request(setup, sql=SECRET_SQL):  # noqa: F811
    from uuid import uuid4

    setup.execute.return_value = QueryResult(
        columns=["ordinal", "name", "physical_type", "nullable"],
        rows=[["1", "total", "decimal(38,12)", "YES"]])
    return SqlInspect(dataset_id=uuid4(), source=SqlSelection(database="analytics", sql=sql))


def _clean(*texts: str) -> None:
    for text in texts:
        for secret in SECRETS:
            assert secret not in text


@pytest.mark.parametrize("failure", [
    StarRocksError(f"Access denied near: {SECRET_SQL}"),
    RuntimeError(f"engine crashed while running {SECRET_SQL}"),
])
async def test_engine_failure_on_secret_sql_is_redacted(setup, caplog, failure):  # noqa: F811
    request = _sql_request(setup)
    setup.execute.side_effect = failure
    caplog.set_level(logging.DEBUG)
    with pytest.raises(Exception) as error:  # noqa: PT011 - both shapes are checked below
        await setup.service.inspect(setup.user, request)
    if isinstance(failure, StarRocksError):
        assert isinstance(error.value, NovaException) and error.value.status_code == 403
        _clean(str(error.value), repr(error.value))
    audit = document_service.write_audit_log
    _clean(caplog.text, repr(audit.call_args_list))
    if isinstance(failure, StarRocksError):
        assert audit.call_args.kwargs["decision"] == "DENY"
        assert set(audit.call_args.kwargs) >= {"object_name", "user_name", "status"}


@pytest.mark.parametrize("sql", [
    SECRET_SQL + " ; DROP TABLE orders",
    SECRET_SQL.replace("FROM orders", "FROM"),
    f"DELETE FROM orders WHERE note = 'password={SECRETS[0]}'",
])
async def test_invalid_secret_sql_is_refused_without_echo(setup, caplog, sql):  # noqa: F811
    request = _sql_request(setup, sql)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(NovaException) as error:
        await setup.service.inspect(setup.user, request)
    assert error.value.status_code == 422
    _clean(str(error.value), repr(error.value), caplog.text,
           repr(document_service.write_audit_log.call_args_list))
    setup.execute.assert_not_awaited()


async def test_a_successful_inspection_returns_no_source_text_in_its_diagnostics(setup):  # noqa: F811
    request = _sql_request(setup)
    result = await setup.service.inspect(setup.user, request)
    # The definition keeps the author's SQL; the derived fields and identity do not.
    _clean(*(field.model_dump_json() for field in result.fields), result.schema_fingerprint,
           result.source.ast_digest, *(d.model_dump_json() for d in result.source.dependencies))


# ── NDV-DATA-011: a saved column renamed in the source ───────────────────


async def test_a_renamed_saved_column_is_stale_not_a_bad_request(
    setup, source,  # noqa: F811
):
    from uuid import uuid4

    from app.modules.viz.contracts.authoring import ObjectInspect, ObjectSelection

    chosen = ObjectInspect(dataset_id=uuid4(), source=ObjectSelection(
        kind="table", database="analytics", object_name="orders", columns=["id", "amount"]))
    # A new selection naming a column that does not exist is the caller's mistake.
    with pytest.raises(NovaException) as mistake:
        await setup.service.inspect(setup.user, chosen.model_copy(update={
            "source": chosen.source.model_copy(update={"columns": ["id", "total"]})}))
    assert mistake.value.status_code == 422
    setup.execute.side_effect = None
    setup.execute.return_value = schema_result(source.columns[:2])
    inspected = await setup.service.inspect(setup.user, chosen)
    document = DatasetDefinition(**inspected.model_dump(), title="Orders")
    renamed = replace(source, columns=(source.columns[0],
                                       replace(source.columns[1], name="total_amount"),
                                       source.columns[2]))
    setup.provider.get_source_schema.return_value = renamed
    selection = module.NativeDatasetService._selection(document)
    with pytest.raises(NovaException, match="SCHEMA_STALE") as error:
        await setup.service._verify_preview_source(setup.user, document, selection)
    assert error.value.status_code == 409


# ── NDV-INTER-009: a drill into detail the role cannot read ──


@pytest.mark.parametrize("partial", [False, True])
async def test_a_denied_drill_detail_is_refused_without_rows(query_setup, partial):
    """Ranger denies the drilled column: the caller gets a refusal and no rows.

    ``partial`` covers an engine that streams some rows before refusing.
    """
    case = query_setup
    fields = {f.source_column: f.field_id for f in case.document.fields}
    drilled = DatasetQueryRequest(
        revision=1, dimensions=[{"field_id": fields["id"]}],
        measures=[{"field_id": fields["amount"], "aggregation": "sum"}],
        filters={"expression": {"type": "predicate", "op": "eq",
                                "field": {"dataset_id": str(case.document.dataset_id),
                                          "dataset_revision": 1, "field_id": fields["id"]},
                                "values": [{"type": "integer", "value": "1"}]}})
    schema = case.execute.side_effect

    async def execute(**kwargs):
        if "row_sink" not in kwargs:
            return await schema(**kwargs)
        if partial:
            sink = kwargs["row_sink"]
            await sink.begin(["d0", "m0"], ())
            await sink.rows([[1, Decimal("10.50")]])
        raise StarRocksError("Access denied; SELECT on column orders.amount is not granted")

    case.execute.side_effect = execute
    with pytest.raises(NovaException) as error:
        await case.service.query(case.user, case.document.dataset_id, drilled)
    assert error.value.status_code == 403
    assert "amount" not in repr(error.value) and "orders" not in str(error.value)
