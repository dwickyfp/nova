"""Author datasets from verified caller-visible sources before opening PG work."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from hashlib import sha256
from typing import Any, Literal
from uuid import UUID, uuid5

import asyncmy.errors
from fastapi import HTTPException

from app.core.database import configured_timezone
from app.core.exceptions import NovaException, SessionExpiredError, StarRocksError
from app.core.security import decrypt_password
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.intelligence.semantic_views import semantic_view_service
from app.modules.query.admission import QueryCapacityError, audit_refusal, query_admission
from app.modules.query.provenance import DatasetQuery, dataset_query
from app.modules.query.repository import QueryRepository
from app.modules.query.service import query_service
from app.observability.metrics import dataviz_read
from app.sql_frontend.analysis.lineage import read_query_sources
from app.sql_frontend.binding.models import BoundColumn, BoundTable, TableName
from app.sql_frontend.binding.starrocks import (
    StarRocksCatalogProvider,
    _identifier,
    query_output_schema,
    source_projection,
)
from app.sql_frontend.context import ExecutionContext
from app.sql_frontend.errors import SemanticError, SQLFrontendError
from app.sql_frontend.parameters import BoundParameters, parameter_slots
from app.sql_frontend.parser import parse_statement_async, run_sql_cpu
from app.sql_frontend.read_queries import (
    parameter_schema_query,
    preview_query,
    schema_query,
    validate_read_query,
)

from .aggregate import plan_aggregate
from .contracts.authoring import (
    DashboardDatasetCreate,
    DatasetCreate,
    DatasetInspect,
    DatasetInspection,
    DatasetPreview,
    DatasetPreviewRequest,
    DatasetUpdate,
    ObjectDatasetUpdate,
    ObjectInspect,
    ObjectInspection,
    ObjectSelection,
    SemanticInspect,
    SemanticInspection,
    SqlInspect,
    SqlInspection,
    SqlSelection,
)
from .contracts.common import LogicalType
from .contracts.datasets import (
    DatasetDefinition,
    DatasetField,
    ObjectSource,
    SemanticSelection,
    SemanticSource,
    SqlSource,
)
from .contracts.queries import MAX_QUERY_OFFSET, DatasetQueryRequest, DatasetQueryResult
from .document_service import (
    current_session,
    document_admission,
    document_transaction,
    document_workspace,
)
from .parameters import bind_parameters
from .preview import PreviewRows
from .semantic_grain import MetricGrainError, SemanticGrain, plan_semantic_grain

SOURCE_TIMEOUT_SECONDS = 30

logger = logging.getLogger(__name__)


@asynccontextmanager
async def source_capacity(
    user: Mapping[str, Any], identity: UUID, *,
    operation: Literal["inspect", "preview", "query"], revision: int | None = None,
) -> AsyncIterator[None]:
    try:
        with dataset_query(DatasetQuery(identity, operation, revision)):
            async with query_admission.slot("viz", source_limit=4):
                yield
    except QueryCapacityError:
        await audit_refusal(
            username=user["username"],
            source="viz",
            target=str(identity),
            session_id=user.get("session_id"),
        )
        raise NovaException(
            "Dataset query capacity reached; retry shortly", status_code=429
        ) from None


@asynccontextmanager
async def held_engine_session(user: Mapping[str, Any]) -> AsyncIterator[None]:
    """One engine session, as this account, for every statement of one request.

    A dataset read checks its source before and after the query, and each check
    reads the catalog several times. Opening a connection per statement made one
    widget cost about fifteen sign-ins on the engine. The request now opens one
    connection with the caller's own credentials and runs everything on it; the
    role is still set before each statement and the session is closed when the
    request ends, timed out or not. A scheduled run already holds its worker's
    session for this account and keeps it.
    """
    from app.core.database import db
    from app.modules.query.service import delegated_connection, delegated_connection_for

    username = str(user.get("username") or "")
    encrypted = user.get("encrypted_password")
    if delegated_connection_for(username) is not None or not isinstance(encrypted, str) \
            or not encrypted:
        yield
        return
    try:
        opened = db.user_conn(username=username, password=decrypt_password(encrypted))
        connection = await opened.__aenter__()
    except asyncmy.errors.OperationalError as error:
        code = error.args[0] if error.args and isinstance(error.args[0], int) else None
        if code == 1045:  # The password changed since sign-in: the session is over.
            raise SessionExpiredError() from None
        raise StarRocksError(f"Connection error: {code}") from None
    except OSError:
        raise StarRocksError("Connection error: unreachable") from None
    try:
        with delegated_connection(username, connection):
            yield
    finally:
        await opened.__aexit__(None, None, None)


def _engine_refusal(error: StarRocksError, refused: str) -> NovaException:
    """403 for anything the engine refused; 503 when the engine did not answer.

    A refusal, a missing object and an analysis error keep one wording, so the
    response never reveals whether a private object exists. An unreachable or
    overloaded engine is not a refusal and must not read as one.
    """
    if error.engine_unavailable:
        logger.warning("Dataviz read failed: engine unavailable (code %s)", error.engine_code)
        return NovaException(
            "The data engine is not responding. Try again in a moment.", status_code=503)
    logger.info("Dataviz read refused by the engine (code %s)", error.engine_code)
    return NovaException(refused, status_code=403)


async def _source(provider: StarRocksCatalogProvider, name: TableName) -> BoundTable:
    try:
        return await provider.get_source_schema(name)
    except SemanticError:
        # Missing and inaccessible catalog objects have the same response and
        # refusal audit; neither reveals whether the private object exists.
        raise NovaException(
            "Source schema is unavailable or not permitted", status_code=403
        ) from None


def _field(dataset_id: UUID, table: BoundTable, column: BoundColumn) -> DatasetField:
    if len(column.name) > 128 or len(column.data_type) > 128:
        raise SemanticError("Selected source metadata exceeds the dataset contract limits")
    data_type = column.sql_type
    logical: LogicalType
    if data_type.kind == "INTEGER" and data_type.bits is not None and data_type.bits <= 64:
        logical = "integer"
    elif (
        data_type.kind == "DECIMAL"
        and data_type.precision is not None
        and data_type.precision <= 38
    ):
        logical = "decimal"
    elif data_type.kind in {"CHAR", "VARCHAR"}:
        logical = "string"
    elif data_type.kind == "BOOLEAN":
        logical = "boolean"
    elif data_type.kind in {"FLOAT", "DOUBLE"}:
        logical = "float"
    elif data_type.kind == "DATE":
        logical = "date"
    elif data_type.kind == "DATETIME":
        logical = "timestamp"
    else:
        raise SemanticError("A selected source type is not supported by the dataset contract")
    if column.nullable is None:
        raise SemanticError("Source nullability is unavailable")
    return DatasetField(
        field_id=str(uuid5(dataset_id, f"{table.physical_id}:{column.name}")),
        name=column.name,
        source_column=column.name,
        logical_type=logical,
        physical_type=column.data_type,
        nullable=column.nullable,
        semantic_role="measure" if logical in {"integer", "decimal", "float"} else "dimension",
        timezone=configured_timezone() if logical == "timestamp" else None,
    )


def _as_saved(
    columns: tuple[BoundColumn, ...], saved: tuple[DatasetField, ...] | None,
) -> tuple[BoundColumn, ...]:
    """Output columns with the saved nullability, when nothing else differs.

    A row filter or column mask on the viewer's role makes the analyzer report
    a NOT NULL column as nullable. Names, order and physical types must still
    match the saved fields exactly; any other difference is left in place and
    reported as a stale schema.
    """
    if saved is None or len(saved) != len(columns) or any(
        column.name != field.source_column or column.data_type != field.physical_type
        for column, field in zip(columns, saved, strict=True)
    ):
        return columns
    return tuple(replace(column, nullable=field.nullable)
                 for column, field in zip(columns, saved, strict=True))


class NativeDatasetService:
    async def _preview_snapshot(
        self, user: Mapping[str, Any], identity: UUID, request: DatasetPreviewRequest
    ) -> dict[str, Any]:
        async with document_transaction(
            user, action="PREVIEW", identity=identity, read_only=True
        ) as repository:
            await repository.require_kind(identity, "dataset")
            snapshot = await repository.read(
                identity, draft=request.draft,
                revision=request.revision if request.draft else None,
            )
        if snapshot["revision"] != request.revision:
            raise NovaException(
                "Dataset revision changed; reload before previewing", status_code=409
            )
        return snapshot

    @staticmethod
    def _selection(document: DatasetDefinition) -> DatasetInspect:
        if (document.metrics or document.relationships
                or document.parameters and not isinstance(document.source, SqlSource)):
            raise SemanticError("This dataset needs a semantic or parameter execution plan")
        source = document.source
        if isinstance(source, ObjectSource):
            return ObjectInspect(
                dataset_id=document.dataset_id,
                source=ObjectSelection(
                    kind=source.kind,
                    catalog=source.catalog,
                    database=source.database,
                    schema_name=source.schema_name or "default",
                    object_name=source.object_name,
                    columns=tuple(field.source_column for field in document.fields),
                ),
            )
        if isinstance(source, SqlSource):
            if source.catalog != "default_catalog" or source.schema_name not in {None, "default"}:
                raise SemanticError("Unsupported SQL dataset catalog")
            return SqlInspect(
                dataset_id=document.dataset_id,
                parameters=document.parameters,
                source=SqlSelection(
                    sql=source.sql,
                    database=source.database,
                ),
            )
        if isinstance(source, SemanticSource):
            return SemanticInspect(
                dataset_id=document.dataset_id,
                source=SemanticSelection(**{
                    key: value for key, value in source.model_dump().items()
                    if key in SemanticSelection.model_fields
                }),
            )
        raise SemanticError("This dataset needs a semantic execution plan")

    async def _verify_preview_source(
        self,
        user: Mapping[str, Any],
        document: DatasetDefinition,
        selection: DatasetInspect,
    ) -> None:
        # Re-inspected as the viewer; their row filters may widen nullability.
        inspected = await self._inspect(user, selection, saved=document.fields)
        if (
            inspected.source != document.source
            or inspected.schema_fingerprint != document.schema_fingerprint
            or inspected.fields != document.fields
        ):
            raise NovaException("SCHEMA_STALE: inspect the source again", status_code=409)

    async def preview(
        self, user: Mapping[str, Any], identity: UUID, request: DatasetPreviewRequest
    ) -> DatasetPreview:
        with dataviz_read("preview"):
            return await self._admitted_preview(user, identity, request)

    async def _admitted_preview(
        self, user: Mapping[str, Any], identity: UUID, request: DatasetPreviewRequest
    ) -> DatasetPreview:
        async with document_admission(user, action="PREVIEW", identity=identity):
            try:
                async with (
                    source_capacity(user, identity, operation="preview", revision=request.revision),
                    asyncio.timeout(SOURCE_TIMEOUT_SECONDS),
                    held_engine_session(user),
                ):
                    return await self._preview(user, identity, request)
            except TimeoutError:
                raise NovaException("Dataset preview timed out", status_code=504) from None
            except SQLFrontendError:
                raise NovaException(
                    "Dataset preview is unavailable or unsupported", status_code=422
                ) from None
            except StarRocksError as error:
                raise _engine_refusal(error, "Dataset is unavailable or not permitted") from None

    async def _preview(
        self, user: Mapping[str, Any], identity: UUID, request: DatasetPreviewRequest
    ) -> DatasetPreview:
        snapshot = await self._preview_snapshot(user, identity, request)
        document = snapshot["definition"]
        if not isinstance(document, DatasetDefinition):
            raise NovaException("Document not found", status_code=404)
        selection = self._selection(document)
        parameters = bind_parameters(document.parameters, request.parameters)
        await self._verify_preview_source(user, document, selection)
        execution: ObjectInspect | SqlInspect
        if isinstance(selection, SemanticInspect):
            execution, _ = await self._semantic_query(user, selection)
        else:
            execution = selection
        columns = tuple(
            BoundColumn(f.source_column, f.physical_type, index, f.nullable)
            for index, f in enumerate(document.fields, 1)
        )
        if isinstance(execution, SqlInspect):
            sql = execution.source.sql
            parsed = await parse_statement_async(sql)
            sql = await run_sql_cpu(
                len(sql),
                preview_query,
                parsed,
                columns,
                limit=request.limit + 1,
                offset=request.offset,
                parameter_specs=parameters.specs if parameters is not None else (),
            )
        else:
            assert isinstance(execution, ObjectInspect)
            source = execution.source
            table = BoundTable(
                TableName(source.object_name, source.database, source.schema_name, source.catalog),
                "VIEW" if source.kind == "view" else "BASE TABLE",
                columns=columns,
            )
            sql = source_projection(
                table,
                tuple(f.source_column for f in document.fields),
                limit=request.limit + 1,
                offset=request.offset,
                text_limit=4097,
                order_by=tuple(f.source_column for f in document.fields),
            )
        session = await current_session(user)
        sink = PreviewRows(document.fields, request.limit)
        result = await query_service.execute(
            sql=sql,
            username=session["username"],
            encrypted_password=user["encrypted_password"],
            database=execution.source.database,
            schema=execution.source.schema_name,
            role=session["active_role"],
            session_id=session["session_id"],
            security_context_version=session["security_context_version"],
            max_rows=request.limit + 1,
            row_sink=sink,
            parameters=parameters,
        )
        if result.error or not result.streamed or not sink.begun or result.rows:
            raise NovaException("Dataset preview could not be completed", status_code=422)
        await self._verify_preview_source(user, document, selection)
        current = await self._preview_snapshot(user, identity, request)
        if any(
            current[key] != snapshot[key]
            for key in (
                "metadata_revision",
                "grants_revision",
                "publication_id",
                "definition",
            )
        ):
            raise NovaException(
                "Dataset access or revision changed; reload before previewing", status_code=409
            )
        next_offset = request.offset + len(sink.values)
        return DatasetPreview(
            dataset_id=identity,
            revision=request.revision,
            fields=document.fields,
            rows=tuple(sink.values),
            offset=request.offset,
            has_more=sink.has_more,
            next_offset=next_offset if sink.has_more and next_offset <= 10_000 else None,
        )

    async def query(
        self, user: Mapping[str, Any], identity: UUID, request: DatasetQueryRequest
    ) -> DatasetQueryResult:
        """Aggregate a pinned dataset revision under the caller's active role."""
        with dataviz_read("query"):
            return await self._admitted_query(user, identity, request)

    async def _admitted_query(
        self, user: Mapping[str, Any], identity: UUID, request: DatasetQueryRequest
    ) -> DatasetQueryResult:
        async with document_admission(user, action="QUERY", identity=identity):
            try:
                async with (
                    source_capacity(user, identity, operation="query", revision=request.revision),
                    asyncio.timeout(SOURCE_TIMEOUT_SECONDS),
                    held_engine_session(user),
                ):
                    return await self._query(user, identity, request)
            except TimeoutError:
                raise NovaException("Dataset query timed out", status_code=504) from None
            except SQLFrontendError:
                raise NovaException(
                    "Dataset query is unavailable or unsupported", status_code=422
                ) from None
            except StarRocksError as error:
                raise _engine_refusal(error, "Dataset is unavailable or not permitted") from None

    async def _query_snapshot(
        self, user: Mapping[str, Any], identity: UUID, request: DatasetQueryRequest
    ) -> dict[str, Any]:
        async with document_transaction(
            user, action="QUERY", identity=identity, read_only=True
        ) as repository:
            await repository.require_kind(identity, "dataset")
            if request.draft:
                snapshot = await repository.read(identity, draft=True, revision=request.revision)
            else:
                # Pinned revisions are visible through a published reference or
                # edit access; row security on revisions decides, not the client.
                definition = await repository.revision(identity, request.revision)
                snapshot = {"revision": request.revision, "definition": definition}
        return snapshot

    async def _query(
        self, user: Mapping[str, Any], identity: UUID, request: DatasetQueryRequest
    ) -> DatasetQueryResult:
        snapshot = await self._query_snapshot(user, identity, request)
        document = snapshot["definition"]
        if not isinstance(document, DatasetDefinition) or snapshot["revision"] != request.revision:
            raise NovaException("Document not found", status_code=404)
        selection = self._selection(document)
        parameters = bind_parameters(document.parameters, request.parameters)
        await self._verify_preview_source(user, document, selection)
        execution: ObjectInspect | SqlInspect
        fields = document.fields
        if isinstance(selection, SemanticInspect):
            execution, model = await self._semantic_query(user, selection)
            try:
                grain = plan_semantic_grain(model, document.fields, request)
            except MetricGrainError as error:
                raise NovaException(str(error), status_code=422) from None
            if grain is not None:
                # Every metric is computed by the semantic layer once per chart group.
                execution, _ = await self._semantic_query(user, selection, grain=grain)
                fields = grain.fields
        else:
            execution = selection
        if isinstance(execution, SqlInspect):
            parsed = await parse_statement_async(execution.source.sql)
            validate_read_query(parsed)
            if parameters is not None:
                parameter_slots(parsed, parameters.specs)
            relation = parsed.statement_context.queryRelation()
            inner = parsed.normalized_sql[relation.start.start : relation.stop.stop + 1]
        else:
            source = execution.source
            qualified = ".".join(
                _identifier(part) for part in (source.catalog, source.database, source.object_name)
            )
            projection = ",".join(_identifier(f.source_column) for f in document.fields)
            inner = f"SELECT {projection} FROM {qualified}"
        plan = await run_sql_cpu(
            len(inner), plan_aggregate, inner, fields, request,
            dataset_id=str(identity),
            reserved_parameters={spec.name for spec in parameters.specs} if parameters else set(),
        )
        specs = (*(parameters.specs if parameters else ()), *plan.specs)
        values = (*(parameters.values if parameters else ()), *plan.values)
        if len(specs) > 32:
            raise NovaException("The query uses too many filter values", status_code=422)
        bound = BoundParameters(tuple(specs), tuple(values)) if specs else None
        session = await current_session(user)
        sink = PreviewRows(plan.output_fields, request.limit, max_bytes=4 * 1_048_576)
        result = await query_service.execute(
            sql=plan.sql,
            username=session["username"],
            encrypted_password=user["encrypted_password"],
            database=execution.source.database,
            schema=execution.source.schema_name,
            role=session["active_role"],
            session_id=session["session_id"],
            security_context_version=session["security_context_version"],
            max_rows=request.limit + 1,
            row_sink=sink,
            parameters=bound,
        )
        if result.error or not result.streamed or not sink.begun or result.rows:
            raise NovaException("Dataset query could not be completed", status_code=422)
        await self._verify_preview_source(user, document, selection)
        next_offset = request.offset + len(sink.values)
        return DatasetQueryResult(
            dataset_id=identity, revision=request.revision, columns=plan.columns,
            rows=tuple(sink.values), truncated=sink.has_more, offset=request.offset,
            next_offset=next_offset if sink.has_more and next_offset <= MAX_QUERY_OFFSET else None,
        )

    async def inspect(
        self, user: Mapping[str, Any], request: DatasetInspect
    ) -> DatasetInspection:
        async with document_admission(user, action="INSPECT_SOURCE", identity=request.dataset_id):
            try:
                async with (
                    source_capacity(user, request.dataset_id, operation="inspect"),
                    asyncio.timeout(SOURCE_TIMEOUT_SECONDS),
                    held_engine_session(user),
                ):
                    return await self._inspect(user, request)
            except TimeoutError:
                raise NovaException("Source inspection timed out", status_code=504) from None
            except SQLFrontendError:
                raise NovaException(
                    "Source schema is unavailable or unsupported", status_code=422
                ) from None
            except StarRocksError as error:
                raise _engine_refusal(error, "Source is unavailable or not permitted") from None

    async def _inspect(
        self,
        user: Mapping[str, Any],
        request: DatasetInspect,
        *,
        saved: tuple[DatasetField, ...] | None = None,
    ) -> DatasetInspection:
        if isinstance(request, SemanticInspect):
            return await self._inspect_semantic(user, request, saved=saved)
        session = await current_session(user)
        encrypted = user.get("encrypted_password")
        if not isinstance(encrypted, str) or not encrypted:
            raise SessionExpiredError()
        # A scheduled run has no password: the task worker's engine session,
        # impersonating exactly this account with its role, answers catalog reads.
        from app.modules.query.service import delegated_connection_for

        delegated = delegated_connection_for(str(session["username"]))
        context = ExecutionContext(
            username=session["username"],
            role=session["active_role"],
            session_id=session["session_id"],
            security_context_version=session["security_context_version"],
            database=request.source.database,
            schema=request.source.schema_name,
            encrypted_password=encrypted,
            connection=delegated,
        )
        provider = StarRocksCatalogProvider(
            QueryRepository(), context,
            (lambda: "") if delegated is not None else (lambda: decrypt_password(encrypted)),
        )
        if isinstance(request, SqlInspect):
            return await self._inspect_sql(user, request, provider, context, encrypted,
                                           saved=saved)
        name = TableName(
            request.source.object_name,
            database=request.source.database,
            schema=request.source.schema_name,
            catalog=request.source.catalog,
        )
        table = await _source(provider, name)
        kind: Literal["table", "view"] = "view" if table.table_type == "VIEW" else "table"
        if kind != request.source.kind or not table.physical_id or not table.schema_version:
            raise SemanticError("Source identity changed")
        columns = table.columns
        if request.source.columns is not None:
            available = {column.name.casefold(): column for column in columns}
            if any(name.casefold() not in available for name in request.source.columns):
                if saved is not None:
                    # A saved dataset's column was renamed or dropped since it
                    # was saved: the source changed, not the request.
                    raise NovaException("SCHEMA_STALE: inspect the source again",
                                        status_code=409)
                raise SemanticError("Selected column is unavailable")
            columns = tuple(available[name.casefold()] for name in request.source.columns)
        if not 1 <= len(columns) <= 512:
            raise SemanticError("Source projection exceeds the field limit")
        # Refuse unsupported source types before executing any plan. The
        # analyzer below distinguishes BOOLEAN from the catalog's tinyint(1).
        for column in columns:
            _field(request.dataset_id, table, column)
        selected = tuple(column.name for column in columns)
        projection = source_projection(table, selected, limit=0)
        parsed = await parse_statement_async(projection)
        sql = await run_sql_cpu(len(projection), schema_query, parsed)
        result = await query_service.execute(
            sql=sql,
            username=context.username,
            encrypted_password=encrypted,
            database=context.database,
            schema=context.schema,
            role=context.role,
            max_rows=512,
            session_id=context.session_id,
            security_context_version=context.security_context_version,
        )
        output_columns = _as_saved(query_output_schema(result), saved)
        if tuple(column.name for column in output_columns) != selected:
            raise SemanticError("Source projection changed during inspection")
        fields = tuple(_field(request.dataset_id, table, column) for column in output_columns)
        observed = await _source(provider, name)
        if (observed.physical_id, observed.schema_version) != (
            table.physical_id,
            table.schema_version,
        ):
            raise NovaException("SCHEMA_STALE: inspect the source again", status_code=409)
        await current_session(user)
        return ObjectInspection(
            dataset_id=request.dataset_id,
            source=ObjectSource(
                kind=kind,
                catalog=name.catalog,
                database=request.source.database,
                schema_name=name.schema,
                object_name=name.table,
                physical_id=table.physical_id,
            ),
            fields=fields,
            schema_fingerprint=sha256(json.dumps({
                "source_schema": table.schema_version,
                "output": [(c.name, c.data_type, c.nullable) for c in output_columns],
            }, separators=(",", ":")).encode()).hexdigest(),
        )

    async def _inspect_sql(
        self,
        user: Mapping[str, Any],
        request: SqlInspect,
        provider: StarRocksCatalogProvider,
        context: ExecutionContext,
        encrypted: str,
        *,
        saved: tuple[DatasetField, ...] | None = None,
    ) -> SqlInspection:
        parsed = await parse_statement_async(request.source.sql)
        if request.parameters:
            sql = await run_sql_cpu(
                len(request.source.sql), parameter_schema_query, parsed,
                tuple(item.specification() for item in request.parameters),
            )
        else:
            sql = await run_sql_cpu(len(request.source.sql), schema_query, parsed)
        names = await run_sql_cpu(
            len(request.source.sql), read_query_sources, parsed, database=request.source.database
        )
        if not names:
            raise SemanticError("A SQL dataset must reference a governed table or view")
        tables = tuple([await _source(provider, name) for name in names])
        result = await query_service.execute(
            sql=sql,
            username=context.username,
            encrypted_password=encrypted,
            database=context.database,
            schema=context.schema,
            role=context.role,
            max_rows=512,
            session_id=context.session_id,
            security_context_version=context.security_context_version,
        )
        columns = _as_saved(query_output_schema(result), saved)
        for table in tables:
            observed = await _source(provider, table.name)
            if (observed.physical_id, observed.schema_version) != (
                table.physical_id,
                table.schema_version,
            ):
                raise NovaException("SCHEMA_STALE: inspect the source again", status_code=409)
        query = parsed.statement_context.queryRelation()
        digest = sha256(
            json.dumps(
                [
                    (token.type, token.text)
                    for token in parsed.visible_tokens
                    if query.start.start <= token.start <= query.stop.stop
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        source = SqlSource(
            sql=request.source.sql,
            ast_digest=digest,
            catalog=request.source.catalog,
            database=request.source.database,
            schema_name=request.source.schema_name,
            dependencies=tuple(
                ObjectSource(
                    kind="view" if table.table_type == "VIEW" else "table",
                    catalog=table.name.catalog,
                    database=table.name.database or request.source.database,
                    schema_name=table.name.schema,
                    object_name=table.name.table,
                    physical_id=table.physical_id or "",
                )
                for table in tables
            ),
        )
        output = BoundTable(
            TableName("output", request.source.database),
            "QUERY",
            columns=columns,
            physical_id=digest,
        )
        fields = tuple(_field(request.dataset_id, output, column) for column in columns)
        fingerprint = sha256(
            json.dumps(
                {
                    "ast": digest,
                    "dependencies": [(table.physical_id, table.schema_version) for table in tables],
                    "columns": [(c.name, c.data_type, c.nullable) for c in columns],
                    **({"parameters": [
                        item.model_dump(mode="json", exclude_defaults=True)
                        for item in request.parameters
                    ]} if request.parameters else {}),
                },
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        await current_session(user)
        return SqlInspection(
            dataset_id=request.dataset_id,
            source=source,
            fields=fields,
            schema_fingerprint=fingerprint,
            parameters=request.parameters,
        )

    async def _semantic_query(
        self, user: Mapping[str, Any], request: SemanticInspect,
        *, grain: SemanticGrain | None = None,
    ) -> tuple[SqlInspect, SemanticModelIR]:
        session = await current_session(user)
        selection = request.source
        try:
            record = await semantic_view_service.get_version_for_agent(
                selection.model_id, selection.model_revision, selection.model_fingerprint,
                {**user, **session},
            )
            model = SemanticModelIR.from_ossie(record["definition"])
            if model.fingerprint != selection.model_fingerprint:
                raise NovaException("Pinned semantic definition has drifted", status_code=409)
            compiled = await run_sql_cpu(
                len(json.dumps(record["definition"])), SemanticCompiler().compile, model,
                SemanticPlan(
                    dimensions=grain.dimensions if grain else selection.dimensions,
                    metrics=grain.metrics if grain else selection.metrics,
                    named_filters=selection.named_filters,
                ),
            )
            execution = SqlInspect(
                dataset_id=request.dataset_id,
                source=SqlSelection(database=record["database_name"], sql=compiled.sql),
            )
        except HTTPException as error:
            raise NovaException(
                "Pinned semantic source is unavailable or not permitted",
                status_code=409 if error.status_code == 409 else 404,
            ) from None
        except (ValueError, TypeError, KeyError):
            raise SemanticError("Selected semantic fields cannot be compiled safely") from None
        return execution, model

    async def _inspect_semantic(
        self, user: Mapping[str, Any], request: SemanticInspect,
        *, saved: tuple[DatasetField, ...] | None = None,
    ) -> SemanticInspection:
        execution, model = await self._semantic_query(user, request)
        inspected = await self._inspect(user, execution, saved=saved)
        assert isinstance(inspected, SqlInspection)
        expected = (*request.source.dimensions, *request.source.metrics)
        if tuple(field.source_column for field in inspected.fields) != expected:
            raise SemanticError("Semantic projection does not match the selected fields")
        observed, _ = await self._semantic_query(user, request)
        if observed != execution:
            raise NovaException("SCHEMA_STALE: inspect the source again", status_code=409)
        fields = []
        for field in inspected.fields:
            metric = model.metric(field.source_column)
            if metric and metric.unit is not None and len(metric.unit) > 64:
                raise SemanticError("Semantic metric unit exceeds the dataset contract limit")
            fields.append(DatasetField(**{
                **field.model_dump(),
                "field_id": str(uuid5(request.dataset_id, ":".join((
                    request.source.model_id, str(request.source.model_revision),
                    model.fingerprint, field.source_column,
                    *(item.physical_id for item in inspected.source.dependencies),
                )))),
                "semantic_role": "measure" if metric else "dimension",
                "unit": metric.unit if metric else None,
            }))
        return SemanticInspection(
            dataset_id=request.dataset_id,
            source=SemanticSource(
                **request.source.model_dump(), database=execution.source.database,
                dependencies=inspected.source.dependencies,
            ),
            fields=tuple(fields),
            schema_fingerprint=sha256(json.dumps({
                "model": request.source.model_dump(mode="json"),
                "schema": inspected.schema_fingerprint,
            }, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        )

    async def create_in_dashboard(
        self, user: Mapping[str, Any], identity: UUID, request: DashboardDatasetCreate, *,
        operation_id: UUID, expected_revision: int,
    ) -> dict[str, Any]:
        async with document_transaction(
            user, action="EDIT", identity=identity, read_only=True,
        ) as repository:
            prior = await repository.dashboard_dataset_receipt(
                identity, request, operation_id=operation_id, expected_revision=expected_revision,
            )
            if prior is not None:
                return prior
            await repository.require_dataset_creation(identity, expected_revision)
        try:
            document = await self._definition(user, request.dataset)
        except (NovaException, HTTPException, SQLFrontendError):
            async with document_transaction(
                user, action="RECEIPT", identity=identity, read_only=True,
            ) as repository:
                prior = await repository.dashboard_dataset_receipt(
                    identity, request, operation_id=operation_id,
                    expected_revision=expected_revision,
                )
                if prior is not None:
                    return prior
            raise
        async with document_transaction(user, action="EDIT", identity=identity) as repository:
            return await repository.create_dashboard_dataset(
                identity, request, document, operation_id=operation_id,
                expected_revision=expected_revision,
            )

    async def create(
        self,
        user: Mapping[str, Any],
        request: DatasetCreate,
        *,
        operation_id: UUID,
    ) -> dict[str, Any]:
        space = await document_workspace(
            user, request.workspace_id, action="CREATE", identity=request.dataset_id, edit=True
        )
        prior = await self._replay(user, request, operation_id, workspace_id=space.id)
        if prior is not None:
            return prior
        try:
            document = await self._definition(user, request)
        except (NovaException, HTTPException, SQLFrontendError):
            prior = await self._replay(user, request, operation_id, workspace_id=space.id)
            if prior is not None:
                return prior
            raise
        async with document_transaction(
            user, action="CREATE", identity=request.dataset_id
        ) as repository:
            return await repository.create(
                space.id, document, operation_id=operation_id, intent=request,
            )

    async def _replay(
        self, user: Mapping[str, Any], request: DatasetUpdate, operation_id: UUID, *,
        workspace_id: str | None = None, expected_revision: int | None = None,
    ) -> dict[str, Any] | None:
        async with document_transaction(
            user, action="RECEIPT", identity=request.dataset_id, read_only=True,
        ) as repository:
            return await repository.dataset_intent_receipt(
                request, operation_id=operation_id, workspace_id=workspace_id,
                expected_revision=expected_revision,
            )

    async def _definition(
        self, user: Mapping[str, Any], request: DatasetUpdate
    ) -> DatasetDefinition:
        inspected = await self.inspect(user, request)
        stale_identity = isinstance(request, ObjectDatasetUpdate) and (
            not isinstance(inspected, ObjectInspection)
            or inspected.source.physical_id != request.expected_physical_id
        )
        if stale_identity or inspected.schema_fingerprint != request.expected_schema_fingerprint:
            raise NovaException("SCHEMA_STALE: inspect the source again", status_code=409)
        return DatasetDefinition(
            dataset_id=request.dataset_id,
            title=request.title,
            source=inspected.source,
            fields=inspected.fields,
            parameters=inspected.parameters if isinstance(inspected, SqlInspection) else (),
            grain=tuple(
                field.field_id for field in inspected.fields
                if isinstance(inspected, SemanticInspection) and field.semantic_role == "dimension"
            ),
            schema_fingerprint=inspected.schema_fingerprint,
        )

    async def update(
        self,
        user: Mapping[str, Any],
        request: DatasetUpdate,
        *,
        operation_id: UUID,
        expected_revision: int,
    ) -> dict[str, Any]:
        identity = request.dataset_id
        prior = await self._replay(user, request, operation_id, expected_revision=expected_revision)
        if prior is not None:
            return prior
        async with document_transaction(
            user, action="EDIT", identity=identity, read_only=True
        ) as repository:
            await repository.require_kind(identity, "dataset")
            snapshot = await repository.read(identity, draft=True)
        current = snapshot["definition"]
        if not isinstance(current, DatasetDefinition):
            raise NovaException("Document not found", status_code=404)
        custom_grain = current.grain and not isinstance(current.source, SemanticSource)
        if (current.metrics or current.relationships or custom_grain
                or current.parameters and not isinstance(request, SqlInspect)):
            raise NovaException(
                "Source edits require review of the dataset's semantic bindings", status_code=409
            )
        try:
            document = await self._definition(user, request)
        except (NovaException, HTTPException, SQLFrontendError):
            prior = await self._replay(
                user, request, operation_id, expected_revision=expected_revision,
            )
            if prior is not None:
                return prior
            raise
        async with document_transaction(user, action="EDIT", identity=identity) as repository:
            return await repository.replace(
                document, operation_id=operation_id, expected_revision=expected_revision,
                intent=request,
            )


native_dataset_service = NativeDatasetService()
