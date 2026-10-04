from app.sql_dialect.grammar import StarRocksParser
from app.sql_frontend.antlr_utils import walk_nodes
from app.sql_frontend.ast.statements import Statement
from app.sql_frontend.binding.relations import identifier, table_name
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.planning.execution import TransactionIntent


def transaction_intent(statement: Statement, context: PlanningContext) -> TransactionIntent | None:
    node = statement.parsed.statement_context
    kind = {
        "InsertStatementContext": "insert",
        "UpdateStatementContext": "update",
        "DeleteStatementContext": "delete",
    }.get(type(node).__name__)
    if kind is None or node.qualifiedName() is None:
        return None
    target = table_name(node.qualifiedName(), context.database)
    nodes = tuple(walk_nodes(node))
    reads = tuple(
        (name.catalog, name.database or "", name.table)
        for child in nodes
        if type(child).__name__ == "TableAtomContext"
        for name in [table_name(child.qualifiedName(), context.database)]
    )
    columns = None
    named_columns = False
    if kind == "insert":
        for clause in node.insertLabelOrColumnAliases():
            aliases = clause.columnAliasesOrByName()
            if aliases is None:
                continue
            if aliases.columnAliases():
                columns = tuple(identifier(item) for item in aliases.columnAliases().identifier())
            else:
                named_columns = True
    unsupported = {
        "CommonTableExpressionContext",
        "FilesContext",
        "TableFunctionContext",
        "StageAtomContext",
        "PartitionNamesContext",
    }
    unproven_relation = any(
        isinstance(child, StarRocksParser.RelationPrimaryContext)
        and type(child).__name__ not in {"TableAtomContext", "SubqueryWithAliasContext"}
        for child in nodes
    )
    return TransactionIntent(
        "overwrite" if kind == "insert" and node.OVERWRITE() else kind,
        (target.catalog, target.database or "", target.table),
        reads,
        columns,
        bool(target.database)
        and not named_columns
        and not unproven_relation
        and not any(type(child).__name__ in unsupported for child in nodes),
    )
