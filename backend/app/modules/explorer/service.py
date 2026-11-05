"""Explorer service — business logic for database explorer."""

from __future__ import annotations

import logging

from app.core.security import decrypt_password
from app.modules.intelligence.entities import entity_registry
from app.modules.intelligence.feature_store import feature_store
from app.modules.intelligence.semantic_views import semantic_view_service
from app.modules.stages.access import StageAccessDenied, check_stage_access

from .repository import INTERNAL_CATALOG, caller_metadata, explorer_repo
from .schemas import (
    CatalogInfo,
    CatalogsResponse,
    ColumnInfo,
    DatabaseObjectsResponse,
    EntitySummary,
    FeatureViewSummary,
    FunctionDetailResponse,
    FunctionSummary,
    MaterializedViewDetailResponse,
    MaterializedViewSummary,
    PartitionInfo,
    PipeDetailResponse,
    PipeSummary,
    SemanticViewSummary,
    StageSummary,
    TableDetailResponse,
    TableProperties,
    TableSummary,
    TaskSummary,
    ViewDetailResponse,
    ViewSummary,
)

log = logging.getLogger(__name__)


class ExplorerService:
    """Orchestrate repository calls and build response models.

    Visibility comes from the caller's engine session; the system pool only
    describes objects that session listed.
    """

    async def get_catalogs(self, user: dict) -> CatalogsResponse:
        async with caller_metadata(user) as caller:
            catalogs = [
                CatalogInfo(
                    name=cat["name"],
                    type=cat["type"],
                    comment=cat["comment"],
                    databases=await caller.databases(cat["name"]),
                )
                for cat in await caller.catalogs()
            ]
        return CatalogsResponse(catalogs=catalogs)

    async def get_database_objects(
        self, database: str, catalog: str | None = None, *, user: dict
    ) -> DatabaseObjectsResponse | None:
        """Objects in one database, or ``None`` when the caller cannot see it."""
        scoped_catalog = catalog or INTERNAL_CATALOG
        internal = scoped_catalog == INTERNAL_CATALOG
        async with caller_metadata(user) as caller:
            if database not in await caller.databases(scoped_catalog):
                return None
            relations = await caller.relations(scoped_catalog, database)
            function_names = await caller.function_names(database) if internal else set()
        base_tables = {name for name, kind in relations.items() if kind == "BASE TABLE"}
        views = {name for name, kind in relations.items() if kind == "VIEW"}

        if internal:
            tables_raw = [
                t for t in await explorer_repo.list_tables(database) if t["name"] in base_tables
            ]
            views_raw = [v for v in await explorer_repo.list_views(database) if v["name"] in views]
            mvs_raw = [
                m for m in await explorer_repo.list_materialized_views(database)
                if m["name"] in views
            ]
            funcs_raw = [
                f for f in await explorer_repo.list_functions(database)
                if f["name"] in function_names
            ]
            pipes_raw = [
                p for p in await explorer_repo.list_pipes(database)
                if _relation_name(p.get("target_table")) in base_tables
            ]
            stages_raw = await explorer_repo.list_stages(database)
            tasks_raw = await explorer_repo.list_tasks(database)
        else:
            tables_raw = [{"name": name} for name in sorted(base_tables)]
            views_raw, mvs_raw, funcs_raw, pipes_raw = [], [], [], []
            stages_raw, tasks_raw = [], []
        password = decrypt_password(user["encrypted_password"])
        authorized_stages = []
        for stage in stages_raw:
            try:
                await check_stage_access(
                    {"database_name": database, "schema_name": stage["schema_name"]},
                    action="read", username=user["username"], password=password,
                    active_role=user.get("active_role"),
                )
            except StageAccessDenied:
                continue
            authorized_stages.append(stage)
        stages_raw = authorized_stages
        entities = [
            entity for entity in await entity_registry.list(user)
            if entity.catalog == scoped_catalog and entity.database == database
        ]
        entity_ids = {entity.id for entity in entities}
        semantic_views = [
            view for view in await semantic_view_service.list(user)
            if view["catalog_name"] == scoped_catalog and view["database_name"] == database
        ]
        feature_views = [
            view for view in await feature_store.list_views(user)
            if view["entity_id"] in entity_ids
        ]

        return DatabaseObjectsResponse(
            database=database,
            tables=[
                TableSummary(
                    name=t["name"],
                    table_model=t.get("table_model"),
                    engine=t.get("engine"),
                    row_count=t.get("row_count"),
                    data_size=t.get("data_size"),
                    create_time=t.get("create_time"),
                )
                for t in tables_raw
            ],
            views=[
                ViewSummary(
                    name=v["name"],
                    definer=v.get("definer"),
                    is_updatable=v.get("is_updatable"),
                )
                for v in views_raw
            ],
            materialized_views=[
                MaterializedViewSummary(
                    name=m["name"],
                    refresh_type=m.get("refresh_type"),
                    is_active=m.get("is_active"),
                    last_refresh_state=m.get("last_refresh_state"),
                    table_rows=m.get("table_rows"),
                    query_rewrite_status=m.get("query_rewrite_status"),
                )
                for m in mvs_raw
            ],
            functions=[
                FunctionSummary(
                    name=f["name"],
                    routine_type=f.get("routine_type"),
                    definer=f.get("definer"),
                    created=f.get("created"),
                )
                for f in funcs_raw
            ],
            pipes=[
                PipeSummary(
                    name=p["name"],
                    state=p.get("state"),
                    target_table=p.get("target_table"),
                    load_status=p.get("load_status"),
                    last_error=p.get("last_error"),
                    created_time=p.get("created_time"),
                )
                for p in pipes_raw
            ],
            stages=[
                StageSummary(
                    name=s["name"],
                    storage_connection=s.get("storage_connection"),
                    base_prefix=s.get("base_prefix"),
                    created_at=s.get("created_at"),
                )
                for s in stages_raw
            ],
            tasks=[
                TaskSummary(
                    name=t["name"],
                    schedule_kind=t.get("schedule_kind"),
                    schedule_expr=t.get("schedule_expr"),
                    timezone=t.get("timezone"),
                    overlap_policy=t.get("overlap_policy"),
                )
                for t in tasks_raw
            ],
            entities=[
                EntitySummary(
                    id=entity.id, name=entity.name, schema_name=entity.schema_name,
                    relation=entity.relation, key_columns=entity.key_columns,
                ) for entity in entities
            ],
            semantic_views=[
                SemanticViewSummary(
                    id=view["id"], name=view["name"], schema_name=view["schema_name"],
                    status=view["status"], active_version=view["active_version"],
                ) for view in semantic_views
            ],
            feature_views=[
                FeatureViewSummary(
                    name=view["name"], entity_id=view["entity_id"],
                    status=view["status"], active_version=view["active_version"],
                ) for view in feature_views
            ],
            summary={
                "tables": len(tables_raw),
                "views": len(views_raw),
                "materialized_views": len(mvs_raw),
                "functions": len(funcs_raw),
                "pipes": len(pipes_raw),
                "stages": len(stages_raw),
                "tasks": len(tasks_raw),
                "entities": len(entities),
                "semantic_views": len(semantic_views),
                "feature_views": len(feature_views),
            },
        )

    async def _visible_relations(self, user: dict, database: str) -> dict[str, str]:
        async with caller_metadata(user) as caller:
            return await caller.relations(INTERNAL_CATALOG, database)

    async def get_table_detail(
        self, database: str, table: str, *, user: dict
    ) -> TableDetailResponse | None:
        if (await self._visible_relations(user, database)).get(table) != "BASE TABLE":
            return None
        raw = await explorer_repo.get_table_detail(database, table)
        if not raw:
            return None
        props_raw = raw.get("properties", {})
        return TableDetailResponse(
            name=raw["name"],
            database=raw["database"],
            columns=[
                ColumnInfo(
                    name=c["name"],
                    ordinal_position=c["ordinal_position"],
                    data_type=c["data_type"],
                    column_type=c.get("column_type"),
                    is_nullable=c.get("is_nullable"),
                    column_key=c.get("column_key"),
                    column_default=c.get("column_default"),
                    extra=c.get("extra"),
                    column_comment=c.get("column_comment"),
                    numeric_precision=c.get("numeric_precision"),
                    numeric_scale=c.get("numeric_scale"),
                    character_maximum_length=c.get("character_maximum_length"),
                )
                for c in raw["columns"]
            ],
            properties=TableProperties(
                table_model=props_raw.get("table_model"),
                primary_key=props_raw.get("primary_key"),
                partition_key=props_raw.get("partition_key"),
                distribute_key=props_raw.get("distribute_key"),
                distribute_type=props_raw.get("distribute_type"),
                distribute_bucket=props_raw.get("distribute_bucket"),
                sort_key=props_raw.get("sort_key"),
                properties=props_raw.get("properties", {}),
                create_ddl=raw.get("create_ddl"),
            ),
            partitions=[
                PartitionInfo(
                    name=p["name"],
                    partition_id=p.get("partition_id"),
                    partition_key=p.get("partition_key"),
                    partition_value=p.get("partition_value"),
                    row_count=p.get("row_count"),
                    data_size=p.get("data_size"),
                    storage_size=p.get("storage_size"),
                    buckets=p.get("buckets"),
                    replication_num=p.get("replication_num"),
                    visible_version=p.get("visible_version"),
                    data_version=p.get("data_version"),
                )
                for p in raw.get("partitions", [])
            ],
            row_count=raw.get("row_count"),
            data_size=raw.get("data_size"),
        )

    async def get_view_detail(
        self, database: str, view: str, *, user: dict
    ) -> ViewDetailResponse | None:
        if (await self._visible_relations(user, database)).get(view) != "VIEW":
            return None
        raw = await explorer_repo.get_view_detail(database, view)
        if not raw:
            return None
        return ViewDetailResponse(**raw)

    async def get_mv_detail(
        self, database: str, mv: str, *, user: dict
    ) -> MaterializedViewDetailResponse | None:
        if (await self._visible_relations(user, database)).get(mv) != "VIEW":
            return None
        raw = await explorer_repo.get_mv_detail(database, mv)
        if not raw:
            return None
        return MaterializedViewDetailResponse(**raw)

    async def get_function_detail(
        self, database: str, fn: str, *, user: dict
    ) -> FunctionDetailResponse | None:
        async with caller_metadata(user) as caller:
            if fn not in await caller.function_names(database):
                return None
        raw = await explorer_repo.get_function_detail(database, fn)
        if not raw:
            return None
        return FunctionDetailResponse(**raw)

    async def get_pipe_detail(
        self, database: str, pipe: str, *, user: dict
    ) -> PipeDetailResponse | None:
        raw = await explorer_repo.get_pipe_detail(database, pipe)
        if not raw:
            return None
        relations = await self._visible_relations(user, database)
        if relations.get(_relation_name(raw.get("target_table"))) != "BASE TABLE":
            return None
        return PipeDetailResponse(**raw)

    async def get_stage_id(self, database: str, stage_name: str) -> str | None:
        """Lookup stage UUID by name + database from CONFIG_STAGES."""
        from app.core.database import db
        result = await db.execute_system(
            "SELECT id FROM NOVA_SYSTEM.CONFIG_STAGES "
            "WHERE name = %s AND database_name = %s",
            (stage_name, database),
        )
        rows = result.get("rows", [])
        return rows[0][0] if len(rows) == 1 else None


def _relation_name(value: object) -> str:
    """Last part of a possibly qualified relation name (``db.table`` → ``table``)."""
    return str(value or "").rsplit(".", 1)[-1].strip("`")


explorer_service = ExplorerService()
