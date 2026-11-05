"""Nova Catalog DDL: connections, foreign catalogs, and catalog refresh.

The statements are Nova grammar (``novaCatalogStatement``); the engine never
sees them. The builder decodes the grammar context, the validator checks the
values and returns a typed, credential-free :class:`CatalogPayload`, and the
executor hands that payload to the catalog service. A ``SECRET`` clause names a
reference; it is never a credential, and a credential-named property is
refused here, before planning.
"""

from __future__ import annotations

from typing import Any

from app.sql_frontend.analysis.effects import PlanEffects
from app.sql_frontend.ast.statements import CatalogStatement, Statement
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import ParsedStatement

_OPERATIONS = {
    "NovaCreateConnectionContext": "create_connection",
    "NovaDropConnectionContext": "drop_connection",
    "NovaShowConnectionsContext": "show_connections",
    "NovaDescribeConnectionContext": "describe_connection",
    "NovaCreateForeignCatalogContext": "create_foreign_catalog",
    "NovaRefreshCatalogContext": "refresh_catalog",
}

#: ``CREATE CONNECTION`` clause names the validator accepts.
CONNECTION_CLAUSES = frozenset({"TYPE", "ENDPOINT", "SECRET", "COMMENT"})


def _identifier(node: Any) -> str | None:
    if node is None:
        return None
    text = node.getText()
    if text.startswith("`") and text.endswith("`"):
        return text[1:-1].replace("``", "`")
    return text


def _literal(parsed: ParsedStatement, node: Any) -> str:
    """The value of a string or identifier node, sliced from the source."""
    text = parsed.normalized_sql[node.start.start : node.stop.stop + 1]
    if text[:1] in {"'", '"'}:
        if "\\" in text:
            raise SemanticError("Backslash escapes are not supported in catalog DDL values")
        return text[1:-1].replace(text[0] * 2, text[0])
    return _identifier(node) or ""


def build_catalog_statement(parsed: ParsedStatement) -> CatalogStatement:
    node = parsed.statement_context
    operation = _OPERATIONS[type(node).__name__]
    clauses: list[tuple[str, str]] = []
    properties: list[tuple[str, str]] = []
    comment = None
    if operation == "create_connection":
        for clause in node.novaConnectionClause():
            value = clause.string() or clause.identifier(1)
            clauses.append(((_identifier(clause.key) or "").upper(), _literal(parsed, value)))
    if operation == "create_foreign_catalog":
        if node.comment():
            comment = _literal(parsed, node.comment().string())
        if node.properties():
            for prop in node.properties().propertyList().property_():
                properties.append((_literal(parsed, prop.key), _literal(parsed, prop.value)))
    return CatalogStatement(
        parsed,
        operation=operation,
        name=_identifier(getattr(node, "catalogName", None) or None),
        connection=_identifier(getattr(node, "connectionName", None) or None),
        schema=_identifier(getattr(node, "schemaName", None) or None),
        relation=_identifier(getattr(node, "relationName", None) or None),
        clauses=tuple(clauses),
        properties=tuple(properties),
        comment=comment,
        if_exists=_has(node, "EXISTS") and not _has(node, "NOT"),
        if_not_exists=_has(node, "NOT") and _has(node, "EXISTS"),
    )


def _has(node: Any, token: str) -> bool:
    """True when the labeled context has ``token`` (absent methods mean no)."""
    return bool(getattr(node, token, lambda: None)())


def catalog_effects(statement: CatalogStatement) -> PlanEffects:
    operation = statement.operation
    if operation in {"show_connections", "describe_connection"}:
        return PlanEffects(reads_data=True)
    if operation == "drop_connection":
        return PlanEffects(writes_metadata=True, drops_objects=True)
    if operation == "create_foreign_catalog":
        return PlanEffects(changes_schema=True, writes_metadata=True, external_io=True)
    if operation == "refresh_catalog":
        return PlanEffects(writes_metadata=True, external_io=True)
    return PlanEffects(writes_metadata=True)


def validate_catalog(statement: Statement, context: Any):
    """Check a catalog statement and return its credential-free payload."""
    from app.modules.catalog.connections import (
        parse_endpoint,
        safe_name,
        validate_connector_type,
        validate_secret_ref,
    )
    from app.modules.catalog.connectors import CatalogError
    from app.modules.catalog.schemas import is_secret_property
    from app.sql_frontend.planning.execution import CatalogPayload

    if not isinstance(statement, CatalogStatement):
        raise TypeError("Catalog validation requires a catalog statement")
    try:
        for value in (statement.name, statement.connection):
            if value is not None:
                safe_name(value)
        kwargs: dict[str, Any] = {}
        if statement.operation == "create_connection":
            clauses: dict[str, str] = {}
            for key, value in statement.clauses:
                if key not in CONNECTION_CLAUSES:
                    raise SemanticError(
                        f"Unknown CREATE CONNECTION clause {key}; expected one of "
                        + ", ".join(sorted(CONNECTION_CLAUSES))
                    )
                if key in clauses:
                    raise SemanticError(f"CREATE CONNECTION clause {key} is repeated")
                clauses[key] = value
            missing = {"TYPE", "ENDPOINT", "SECRET"} - set(clauses)
            if missing:
                raise SemanticError(
                    "CREATE CONNECTION requires " + ", ".join(sorted(missing))
                )
            connector_type = validate_connector_type(clauses["TYPE"])
            parse_endpoint(connector_type, clauses["ENDPOINT"])
            kwargs.update(
                connector_type=connector_type,
                endpoint=clauses["ENDPOINT"],
                secret_ref=validate_secret_ref(clauses["SECRET"]),
                comment=clauses.get("COMMENT"),
            )
        if statement.operation == "create_foreign_catalog":
            for key, _ in statement.properties:
                if is_secret_property(key):
                    raise SemanticError(
                        f"Property '{key}' carries a credential; it comes from the "
                        "connection's secret reference"
                    )
            kwargs.update(properties=statement.properties, comment=statement.comment)
    except CatalogError as exc:
        raise SemanticError(str(exc)) from None
    return CatalogPayload(
        0,
        operation=statement.operation,
        name=statement.name or statement.connection or "",
        connection=statement.connection,
        schema=statement.schema,
        relation=statement.relation,
        if_exists=statement.if_exists,
        if_not_exists=statement.if_not_exists,
        **kwargs,
    )
