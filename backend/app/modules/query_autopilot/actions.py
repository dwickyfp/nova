"""Closed action templates; every returned statement still passes Nova's SQL frontend."""

from __future__ import annotations

from app.common.identifiers import check_identifier
from app.modules.query_autopilot.models import ActionKind, Candidate
from app.sql_frontend.autopilot import (
    materialized_view_definition,
    materialized_view_shape,
    quote_path,
)
from app.sql_frontend.parser import parse_statement


def action_sql(
    candidate: Candidate, *, targets: tuple[str, ...] | None = None, sample_sql: str | None = None
) -> tuple[str, ...]:
    tables = tuple(quote_path(t) for t in (targets or candidate.targets))
    params = candidate.parameters
    if candidate.kind == ActionKind.STATISTICS:
        if params:
            raise ValueError("basic_statistics_accepts_no_overrides")
        statements = tuple(f"ANALYZE TABLE {table} WITH SYNC MODE" for table in tables)
    elif candidate.kind == ActionKind.HISTOGRAM:
        columns = params.get("columns", [])
        if not columns or len(columns) > 8 or set(params) - {"columns", "buckets"}:
            raise ValueError("bounded_histogram_columns_required")
        buckets = int(params.get("buckets", 64))
        if not 1 <= buckets <= 256:
            raise ValueError("histogram_bucket_budget")
        names = ",".join(f"`{check_identifier(c, field='histogram column')}`" for c in columns)
        statements = tuple(
            f"ANALYZE TABLE {table} UPDATE HISTOGRAM ON {names} WITH {buckets} BUCKETS"
            for table in tables
        )
    elif candidate.kind == ActionKind.MATERIALIZED_VIEW:
        if sample_sql is None or not materialized_view_shape(sample_sql).eligible:
            raise ValueError("unsupported_materialized_view_shape")
        if set(params) - {"name"}:
            raise ValueError("unsupported_materialized_view_parameters")
        name = check_identifier(
            params.get("name") or f"nova_ap_{candidate.id.replace('-', '')[:24]}",
            field="materialized view",
        )
        if not name.startswith("nova_ap_"):
            raise ValueError("autopilot_owned_object_required")
        statements = (
            (
                f"CREATE MATERIALIZED VIEW `{name}` DISTRIBUTED BY RANDOM "
                f"REFRESH MANUAL AS {materialized_view_definition(sample_sql)}"
            ),
            f"REFRESH MATERIALIZED VIEW `{name}` WITH SYNC MODE",
        )
    elif candidate.kind == ActionKind.ADD_INDEX:
        from app.modules.indexes.router import CreateIndexRequest, build_create_index_sql

        if len(tables) != 1 or set(params) - {"column", "name", "index_kind", "parser"}:
            raise ValueError("single_enrolled_index_target_required")
        parts = tables[0].split(".")
        if len(parts) == 1:
            parts.insert(0, f"`{candidate.scope.database}`")
        if len(parts) != 2:
            raise ValueError("native_catalog_index_required")
        name = params.get("name") or f"nova_ap_{candidate.id.replace('-', '')[:24]}"
        if not str(name).startswith("nova_ap_"):
            raise ValueError("autopilot_owned_object_required")
        request = CreateIndexRequest(
            database=parts[0].strip("`"),
            table=parts[1].strip("`"),
            index_name=name,
            column=params["column"],
            kind=params.get("index_kind", "BITMAP"),
            parser=params.get("parser"),
            imp_lib=None,
            dict_gram_num=None,
        )
        statements = (build_create_index_sql(request),)
    elif candidate.kind == ActionKind.RESOURCE_GROUP:
        from app.modules.resource_groups.service import build_alter_resource_group_sql

        if set(params) != {"resource_group", "properties"}:
            raise ValueError("resource_group_and_properties_required")
        statements = (
            build_alter_resource_group_sql(params["resource_group"], params["properties"]),
        )
    elif candidate.kind == ActionKind.PLAN_BASELINE:
        if sample_sql is None or params:
            raise ValueError("baseline_requires_validated_replay_sample")
        statements = (f"CREATE GLOBAL BASELINE USING {sample_sql}",)
    elif candidate.kind == ActionKind.REFRESH_POLICY:
        if set(params) != {"name", "interval_minutes"}:
            raise ValueError("refresh_name_and_interval_required")
        name = check_identifier(params["name"], field="materialized view")
        minutes = int(params["interval_minutes"])
        if not name.startswith("nova_ap_") or not 5 <= minutes <= 1440:
            raise ValueError("bounded_autopilot_owned_refresh_required")
        statements = (
            f"ALTER MATERIALIZED VIEW `{name}` REFRESH ASYNC EVERY(INTERVAL {minutes} MINUTE)",
        )
    elif candidate.kind == ActionKind.NATIVE_FEEDBACK:
        if sample_sql is None:
            raise ValueError("replay_sample_required")
        statements = (f"ALTER PLAN ADVISOR ADD {sample_sql}",)
    else:
        raise ValueError("action_requires_supported_owner_adapter")
    for statement in statements:
        parse_statement(statement)
    return statements
