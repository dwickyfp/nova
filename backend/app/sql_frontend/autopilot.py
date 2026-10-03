"""Structural replay lowering and bounded optimization templates."""

from __future__ import annotations

from dataclasses import dataclass

from app.common.identifiers import check_identifier
from app.sql_frontend.antlr_utils import walk_nodes
from app.sql_frontend.fingerprint import fingerprint
from app.sql_frontend.parser import parse_statement


def quote_path(path: str) -> str:
    parsed = parse_statement(f"SELECT * FROM {path}")
    nodes = list(walk_nodes(parsed.statement_context))
    atoms = [n for n in nodes if type(n).__name__ == "TableAtomContext"]
    if len(atoms) != 1 or atoms[0].alias is not None:
        raise ValueError("Expected one table identifier")
    name = atoms[0].qualifiedName()
    parts = [
        check_identifier(
            parsed.normalized_sql[i.start.start : i.stop.stop + 1].strip("`"), field="table"
        )
        for i in name.identifier()
    ]
    if len(parts) not in {1, 2, 3}:
        raise ValueError("Expected a qualified table identifier")
    if parsed.normalized_sql[name.stop.stop + 1 :].strip():
        raise ValueError("Unexpected table identifier suffix")
    return ".".join(f"`{part}`" for part in parts)


def map_snapshot(
    sql: str, mapping: dict[str, str], source_database: str, sandbox_database: str
) -> str:
    shape = fingerprint(sql)
    if not shape.replay_eligible:
        raise ValueError(shape.reason)
    parsed = parse_statement(sql)
    changes = []
    qualifiers = {}
    aliases = set()
    nodes = list(walk_nodes(parsed.statement_context))
    for node in nodes:
        kind = type(node).__name__
        if kind == "CommonTableExpressionContext":
            raise ValueError("CTE snapshot mapping is not yet certified")
        if kind != "TableAtomContext":
            continue
        name = node.qualifiedName()
        parts = [
            parsed.normalized_sql[i.start.start : i.stop.stop + 1].strip("`")
            for i in name.identifier()
        ]
        full = ".".join(parts) if len(parts) > 1 else f"{source_database}.{parts[0]}"
        target = mapping.get(full) or mapping.get(".".join(parts))
        if target is None and (
            len(parts) == 2
            and parts[0] == source_database
            or len(parts) == 3
            and parts[:2] == ["default_catalog", source_database]
        ):
            target = mapping.get(parts[-1]) or mapping.get(f"{source_database}.{parts[-1]}")
        if target is None:
            raise ValueError("table_not_enrolled")
        quoted = quote_path(target)
        if not quoted.startswith(
            f"`{check_identifier(sandbox_database, field='sandbox database')}`."
        ):
            raise ValueError("snapshot_mapping_outside_sandbox")
        changes.append((name.start.start, name.stop.stop + 1, quoted))
        if node.alias is not None:
            aliases.add(node.alias.getText().strip("`"))
        else:
            database = parts[-2] if len(parts) > 1 else source_database
            catalog = parts[0] if len(parts) == 3 else "default_catalog"
            for qualifier in (
                (parts[-1],),
                (database, parts[-1]),
                (catalog, database, parts[-1]),
            ):
                qualifiers.setdefault(qualifier, set()).add(quoted)
    for node in nodes:
        if type(node).__name__ != "DereferenceContext":
            continue
        if type(node.parentCtx).__name__ == "DereferenceContext":
            continue
        fields, current = [], node
        while type(current).__name__ == "DereferenceContext":
            field = current.fieldName
            token = field.getText() if field else current.DOT_IDENTIFIER().getText()[1:]
            fields.append(check_identifier(token.strip("`"), field="column"))
            current = current.base
        if type(current).__name__ != "ColumnRefContext":
            continue
        base = current.columnReference().identifier().getText().strip("`")
        parts = (base, *reversed(fields))
        if parts[0] in aliases:
            continue
        targets = qualifiers.get(parts[:-1])
        if targets:
            if (
                len(targets) != 1
                or sum(type(n).__name__ == "QuerySpecificationContext" for n in nodes) != 1
            ):
                raise ValueError("qualified_snapshot_column_scope_ambiguous")
            changes.append(
                (node.start.start, node.stop.stop + 1, next(iter(targets)) + f".`{parts[-1]}`")
            )
    for start, end, replacement in sorted(changes, reverse=True):
        sql = sql[:start] + replacement + sql[end:]
    if not changes:
        raise ValueError("no_enrolled_relation")
    return sql


@dataclass(frozen=True)
class MVShape:
    eligible: bool
    reason: str | None
    grouping: tuple[str, ...] = ()
    aggregates: tuple[str, ...] = ()


def materialized_view_shape(sql: str) -> MVShape:
    shape = fingerprint(sql)
    if not shape.replay_eligible:
        return MVShape(False, shape.reason)
    parsed = parse_statement(sql)
    nodes = list(walk_nodes(parsed.statement_context))
    kinds = {type(n).__name__ for n in nodes}
    if len([n for n in nodes if type(n).__name__ == "QuerySpecificationContext"]) != 1:
        return MVShape(False, "multiple_query_blocks")
    if kinds & {
        "OverContext",
        "SetOperationContext",
        "CommonTableExpressionContext",
        "CubeContext",
        "RollupContext",
        "MultipleGroupingSetsContext",
    }:
        return MVShape(False, "unsupported_aggregate_shape")
    groups = [n for n in nodes if type(n).__name__ == "SingleGroupingSetContext"]
    aggregates = [n for n in nodes if type(n).__name__ == "AggregationFunctionContext"]
    if len(groups) != 1 or not aggregates:
        return MVShape(False, "grouped_aggregate_required")
    for aggregate in aggregates:
        tokens = parsed.tokens.tokens[aggregate.start.tokenIndex : aggregate.stop.tokenIndex + 1]
        if tokens[0].text.upper() not in {"COUNT", "SUM", "MIN", "MAX", "AVG"} or any(
            t.text.upper() == "DISTINCT" for t in tokens
        ):
            return MVShape(False, "non_decomposable_aggregate")
    for node in nodes:
        if type(node).__name__ == "JoinRelationContext":
            tokens = [
                t.text.upper()
                for t in parsed.tokens.tokens[node.start.tokenIndex : node.stop.tokenIndex + 1]
            ]
            if (
                any(
                    t
                    in {
                        "LEFT",
                        "RIGHT",
                        "FULL",
                        "CROSS",
                        "SEMI",
                        "ANTI",
                        "USING",
                        "OR",
                        ">",
                        "<",
                        "!=",
                        "<>",
                    }
                    for t in tokens
                )
                or "=" not in tokens
            ):
                return MVShape(False, "inner_equijoin_required")
    return MVShape(
        True,
        None,
        tuple(
            sql[n.start.start : n.stop.stop + 1] for n in groups[0].expressionList().expression()
        ),
        tuple(sql[n.start.start : n.stop.stop + 1] for n in aggregates),
    )


def materialized_view_definition(sql: str) -> str:
    """Retain grouping keys in the MV for governed filter/rewrite evaluation."""
    shape = materialized_view_shape(sql)
    if not shape.eligible:
        raise ValueError(shape.reason)
    parsed = parse_statement(sql)
    nodes = list(walk_nodes(parsed.statement_context))
    query = next(n for n in nodes if type(n).__name__ == "QuerySpecificationContext")
    group = next(n for n in nodes if type(n).__name__ == "SingleGroupingSetContext")
    selected = query.selectItem()
    if any(type(item).__name__ != "SelectSingleContext" for item in selected):
        raise ValueError("explicit_aggregate_projection_required")

    def key(node):
        return tuple(
            token.text.casefold()
            for token in parsed.visible_tokens
            if node.start.tokenIndex <= token.tokenIndex <= node.stop.tokenIndex
        )

    projections = {key(item.expression()) for item in selected}
    aliases = {key(item.identifier()) for item in selected if item.identifier() is not None}
    grouping = group.expressionList().expression()
    occupied = {token.text.strip("`").casefold() for token in parsed.visible_tokens}
    additions = []
    for index, expression in enumerate(grouping):
        tokens = key(expression)
        if tokens in projections or tokens in aliases:
            continue
        if len(tokens) == 1 and tokens[0].isdecimal():
            if not 1 <= int(tokens[0]) <= len(selected):
                raise ValueError("grouping_position_unavailable")
            continue
        alias = f"nova_ap_group_{index}"
        if alias in occupied:
            raise ValueError("generated_grouping_alias_conflict")
        source = parsed.normalized_sql[expression.start.start : expression.stop.stop + 1]
        additions.append(f"{source} AS `{alias}`")
    if not additions:
        return sql
    end = selected[-1].stop.stop + 1
    return (
        parsed.normalized_sql[:end] + ", " + ", ".join(additions)
        + parsed.normalized_sql[end:]
    )


def deterministic_order(sql: str, columns: list[str], rows: list[list]) -> bool:
    """Certify observed ordering keys; ambiguous ties cannot prove ordered equivalence."""
    import json

    parsed = parse_statement(sql)
    sorts = parsed.statement_context.queryRelation().queryNoWith().sortItem()
    if not sorts:
        return False
    keys = []
    for item in sorts:
        expr = item.expression()
        tokens = [
            t
            for t in parsed.visible_tokens
            if expr.start.tokenIndex <= t.tokenIndex <= expr.stop.tokenIndex
        ]
        if len(tokens) == 1 and tokens[0].text.isdecimal():
            index = int(tokens[0].text) - 1
            if index < 0 or index >= len(columns):
                raise ValueError("ordering_position_unavailable")
            keys.append(index)
        else:
            text = parsed.normalized_sql[expr.start.start : expr.stop.stop + 1]
            matches = [
                i for i, name in enumerate(columns) if name == text or name == text.strip("`")
            ]
            if len(matches) != 1:
                raise ValueError("ordering_expression_unproven")
            keys.append(matches[0])
    seen = {}
    for row in rows:
        key = json.dumps([row[i] for i in keys], default=str)
        whole = json.dumps(row, default=str)
        if key in seen and seen[key] != whole:
            raise ValueError("ordering_ties_are_not_deterministic")
        seen[key] = whole
    return True


def clone_materialized_view(
    definition: str,
    name: str,
    mapping: dict[str, str],
    source_database: str,
    sandbox_database: str,
) -> str:
    """Clone an owned V1 MV's query and refresh schedule onto enrolled relations."""
    parsed = parse_statement(definition)
    context = parsed.statement_context
    if type(context).__name__ != "CreateMaterializedViewStatementContext":
        raise ValueError("materialized_view_definition_required")
    name = check_identifier(name, field="materialized view")
    if not name.startswith("nova_ap_"):
        raise ValueError("autopilot_owned_object_required")
    descriptors = context.materializedViewDesc()
    distributions = [d.distributionDesc() for d in descriptors if d.distributionDesc()]
    schedules = [d.refreshSchemeDesc() for d in descriptors if d.refreshSchemeDesc()]
    if (
        len(distributions) != 1
        or len(schedules) != 1
        or any(d.mvPartitionExprs() or d.orderByDesc() for d in descriptors)
        or not any(
            token.text.upper() == "RANDOM"
            for token in parsed.visible_tokens
            if distributions[0].start.tokenIndex
            <= token.tokenIndex
            <= distributions[0].stop.tokenIndex
        )
    ):
        raise ValueError("materialized_view_clone_design_unsupported")
    schedule = schedules[0]
    tokens = [
        t.text.upper()
        for t in parsed.visible_tokens
        if schedule.start.tokenIndex <= t.tokenIndex <= schedule.stop.tokenIndex
    ]
    if "START" in tokens or "INCREMENTAL" in tokens or "DEFERRED" in tokens:
        raise ValueError("materialized_view_clone_schedule_unsupported")
    query = context.queryStatement()
    sql = parsed.normalized_sql[query.start.start : query.stop.stop + 1]
    if not materialized_view_shape(sql).eligible:
        raise ValueError("materialized_view_clone_shape_unsupported")
    replay = map_snapshot(sql, mapping, source_database, sandbox_database)
    columns = [
        check_identifier(column.identifier().getText().strip("`"), field="MV column")
        for column in context.columnNameWithComment()
    ]
    projection = "(" + ",".join(f"`{column}`" for column in columns) + ")" if columns else ""
    refresh = parsed.normalized_sql[schedule.start.start : schedule.stop.stop + 1]
    statement = (
        f"CREATE MATERIALIZED VIEW `{name}` {projection} DISTRIBUTED BY RANDOM "
        f"{refresh} AS {replay}"
    )
    parse_statement(statement)
    return statement
