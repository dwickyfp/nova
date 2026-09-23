"""Conservative SQL compatibility checks for verified semantic examples."""

from __future__ import annotations

from typing import Any

from app.modules.agents.semantic.expressions import walk
from app.modules.agents.semantic.planning import SemanticPlanError
from app.modules.query.dialect.parser import _parse_tree
from app.sql_dialect.grammar import StarRocksParser


def verify_sql_compatibility(compiled_sql: str, supplied_sql: str) -> None:
    expected = _signature(compiled_sql)
    actual = _signature(supplied_sql)
    differences = [name for name in expected if expected[name] != actual[name]]
    if differences:
        raise SemanticPlanError(
            "Verified SQL cannot be matched to the semantic plan: "
            + ", ".join(differences)
            + ". Use the compiled query or a structurally equivalent query."
        )


def _signature(sql: str) -> dict[str, Any]:
    if len(sql) > 64000:
        raise SemanticPlanError("Verified SQL exceeds the verification size limit.")
    stream, tree, errors = _parse_tree(sql)
    first_token = next((token.text for token in stream.tokens if token.channel == 0), "")
    if first_token.upper() != "SELECT":
        raise SemanticPlanError("Verified SQL must be a SELECT, not an execution wrapper.")
    nodes = list(walk(tree))
    queries = [
        node for node in nodes if isinstance(node, StarRocksParser.QuerySpecificationContext)
    ]
    if errors or len(queries) != 1:
        raise SemanticPlanError(
            "Verification requires one parseable SELECT without nested queries."
        )
    query = queries[0]
    if query.qualifyFunction is not None:
        raise SemanticPlanError("QUALIFY requires a dedicated semantic verification rule.")
    tables = [node for node in nodes if isinstance(node, StarRocksParser.TableAtomContext)]
    aliases: dict[str, str] = {}
    sources: list[str] = []
    for table in tables:
        if any(
            getattr(table, name)() is not None
            for name in (
                "queryPeriod",
                "partitionNames",
                "tabletList",
                "replicaList",
                "sampleClause",
            )
        ):
            raise SemanticPlanError("Table sampling and source modifiers cannot be verified.")
        source = ".".join(
            part.strip("`").lower() for part in table.qualifiedName().getText().split(".")
        )
        sources.append(source)
        alias = (
            table.alias.getText().strip("`").lower() if table.alias else source.rsplit(".", 1)[-1]
        )
        if alias in aliases:
            raise SemanticPlanError("Repeated table aliases cannot be verified safely.")
        aliases[alias] = source
    default = sources[0] if len(sources) == 1 else None

    def canonical(node: Any) -> Any:
        if node is None:
            return None
        if isinstance(node, StarRocksParser.DereferenceContext):
            parts = node.getText().split(".")
            if len(parts) == 2:
                qualifier, field = (part.strip("`").lower() for part in parts)
                return ("column", aliases.get(qualifier, qualifier), field)
        if isinstance(node, StarRocksParser.ColumnReferenceContext):
            return ("column", default, node.getText().strip("`").lower())
        if isinstance(node, StarRocksParser.IdentifierContext):
            return node.getText().strip("`").lower()
        children = getattr(node, "children", None)
        if children:
            values = tuple(canonical(child) for child in children)
            return values[0] if len(values) == 1 else values
        text = node.getText()
        return text if text.startswith(("'", '"')) else text.lower()

    projections = []
    for item in query.selectItem():
        if not isinstance(item, StarRocksParser.SelectSingleContext):
            raise SemanticPlanError("Wildcard projections cannot verify semantic output.")
        projections.append(canonical(item.expression()))
    return {
        "sources": tuple(sorted(sources)),
        "projections": tuple(projections),
        "filters": canonical(query.where),
        "grouping": canonical(query.groupingElement()),
        "having": canonical(query.having),
        "distinct": canonical(query.setQuantifier()),
        "joins": tuple(
            canonical(node)
            for node in nodes
            if type(node).__name__
            in {
                "JoinCriteriaContext",
                "CrossOrInnerJoinTypeContext",
                "OuterAndSemiJoinTypeContext",
                "AsofJoinTypeContext",
            }
        ),
        "ordering_and_limit": tuple(
            canonical(node)
            for node in nodes
            if type(node).__name__ in {"SortItemContext", "LimitElementContext"}
        ),
    }
