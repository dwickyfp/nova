"""``MERGE INTO``: Nova DML lowered to a Primary Key upsert.

The pinned engine has no ``MERGE``. For a Primary Key table it has the next best
thing: an ``INSERT`` of a row whose key already exists updates that row. A
``MERGE`` is therefore lowered to one ``INSERT INTO target (key, written
columns) SELECT ...`` whose rows are the updated rows and the inserted rows, read from
``source LEFT JOIN target ON <condition>``. It is one engine load, so it is
written completely or not at all. The source may be any relation the caller can
read, including a table, view or query over an external catalog.

``WHEN MATCHED ... THEN DELETE`` adds a ``DELETE`` that runs before the upsert.
That plan is several engine statements and declared best-effort: the engine has
no multi-statement transaction for ``DELETE`` on this deployment.

The upsert is a partial update (``enable_insert_partial_update``): it writes the
key and the columns some clause sets, nothing else. A matched row keeps its other
columns and a new row gets their table defaults, so a column the MERGE never
mentions is never rewritten, including one the caller sees masked.

The meaning follows the SQL standard:

* ``ON`` equates every primary key column of the target with a source value, so
  a source row matches at most one target row, and an inserted row can never
  replace a row that did not match.
* Clauses are tried in order; the first whose condition holds applies, and a
  row no clause selects is left alone.
* Two source rows acting on the same key fail the whole statement before
  anything is written.
"""

from __future__ import annotations

import re
from typing import Any

from app.common.sql_guard import redact_sql_credentials
from app.sql_frontend.analysis.effects import PlanEffects
from app.sql_frontend.antlr_utils import walk_nodes
from app.sql_frontend.ast.statements import MergeClause, MergeStatement, Statement
from app.sql_frontend.binding.models import BoundColumn, TableName
from app.sql_frontend.binding.relations import identifier
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import ParsedStatement
from app.sql_frontend.planning.execution import (
    Atomicity,
    CompositePlan,
    EngineSqlPlan,
    ExecutionPlan,
)
from app.sql_frontend.planning.logical import LogicalPlan

INTERNAL_CATALOG = "default_catalog"

_KEY_EXAMPLE = "for example ON t.id = s.id"


def _text(parsed: ParsedStatement, node: Any) -> str:
    return parsed.normalized_sql[node.start.start : node.stop.stop + 1]


def _ref_parts(node: Any) -> list[str] | None:
    """The identifier parts of a column reference (``a``, ``t.a``, ``db.t.a``)."""
    kind = type(node).__name__
    if kind == "ColumnRefContext":
        return [identifier(node.columnReference().identifier())]
    if kind == "DereferenceContext":
        base = _ref_parts(node.base)
        if base is None:
            return None
        if node.fieldName is not None:
            return [*base, identifier(node.fieldName)]
        return [*base, node.DOT_IDENTIFIER().getText()[1:]]
    return None


def _whole_reference(side: Any) -> list[str] | None:
    """The column reference ``side`` consists of, if it is nothing else."""
    for node in walk_nodes(side):
        if (
            type(node).__name__ in {"DereferenceContext", "ColumnRefContext"}
            and node.start.tokenIndex == side.start.tokenIndex
            and node.stop.tokenIndex == side.stop.tokenIndex
        ):
            return _ref_parts(node)
    return None


class _Target:
    """Recognises references to the target relation by its alias or name."""

    def __init__(self, parts: tuple[str, ...], alias: str | None) -> None:
        folded = tuple(part.casefold() for part in parts)
        self.qualifiers = (
            {(alias.casefold(),)}
            if alias
            else {folded[index:] for index in range(len(folded))}
        )

    def column(self, parts: list[str] | None) -> str | None:
        if not parts or len(parts) < 2:
            return None
        qualifier = tuple(part.casefold() for part in parts[:-1])
        return parts[-1] if qualifier in self.qualifiers else None

    def read_by(self, node: Any) -> bool:
        return node is not None and any(
            self.column(_ref_parts(child)) is not None
            for child in walk_nodes(node)
            if type(child).__name__ == "DereferenceContext"
        )


def _equalities(expression: Any) -> list[Any] | None:
    kind = type(expression).__name__
    if kind == "LogicalBinaryContext" and expression.operator.text.upper() in {"AND", "&&"}:
        left, right = _equalities(expression.left), _equalities(expression.right)
        return None if left is None or right is None else left + right
    if kind == "ExpressionDefaultContext" and expression.BINARY() is None:
        comparison = expression.booleanExpression()
        if (
            type(comparison).__name__ == "ComparisonContext"
            and comparison.comparisonOperator().getText() == "="
        ):
            return [comparison]
    return None


def _keys(parsed: ParsedStatement, condition: Any, target: _Target):
    comparisons = _equalities(condition)
    if comparisons is None:
        return None
    keys: list[tuple[str, str]] = []
    for comparison in comparisons:
        left, right = comparison.left, comparison.right
        if (column := target.column(_whole_reference(left))) and not target.read_by(right):
            keys.append((column, _text(parsed, right)))
        elif (column := target.column(_whole_reference(right))) and not target.read_by(left):
            keys.append((column, _text(parsed, left)))
        else:
            return None
    return tuple(keys)


def build_merge_statement(parsed: ParsedStatement) -> MergeStatement:
    node = parsed.statement_context
    target_parts = tuple(identifier(item) for item in node.target.identifier())
    target_alias = identifier(node.targetAlias) if node.targetAlias is not None else None
    target = _Target(target_parts, target_alias)
    source = node.novaMergeSource()
    query = type(source).__name__ == "NovaMergeSourceQueryContext"
    relation = source.subquery() if query else source.qualifiedName()
    clauses = []
    for clause in node.novaMergeClause():
        kind = {
            "NovaMergeUpdateContext": "update",
            "NovaMergeDeleteContext": "delete",
            "NovaMergeInsertContext": "insert",
        }[type(clause).__name__]
        condition = clause.condition
        if kind == "update":
            assignments = tuple(
                (
                    identifier(item.identifier()),
                    _text(parsed, value)
                    if (value := item.expressionOrDefault().expression()) is not None
                    else None,
                )
                for item in clause.assignmentList().assignment()
            )
            clauses.append(
                MergeClause(
                    kind,
                    _text(parsed, condition) if condition is not None else None,
                    assignments=assignments,
                )
            )
            continue
        if kind == "delete":
            clauses.append(
                MergeClause(kind, _text(parsed, condition) if condition is not None else None)
            )
            continue
        values = [item.expression() for item in clause.expressionOrDefault()]
        clauses.append(
            MergeClause(
                kind,
                _text(parsed, condition) if condition is not None else None,
                columns=tuple(identifier(item) for item in clause.columnAliases().identifier())
                if clause.columnAliases() is not None
                else None,
                values=tuple(
                    _text(parsed, value) if value is not None else None for value in values
                ),
                reads_target=target.read_by(condition)
                or any(target.read_by(value) for value in values),
            )
        )
    return MergeStatement(
        parsed,
        target=target_parts,
        target_sql=_text(parsed, node.target),
        target_alias=target_alias,
        source_sql=_text(parsed, relation),
        source_alias=identifier(source.alias) if source.alias is not None else None,
        source_name=()
        if query
        else tuple(identifier(item) for item in source.qualifiedName().identifier()),
        on_sql=_text(parsed, node.condition),
        keys=_keys(parsed, node.condition, target),
        clauses=tuple(clauses),
    )


def merge_effects(statement: MergeStatement) -> PlanEffects:
    kinds = {clause.kind for clause in statement.clauses}
    return PlanEffects(
        reads_data=True,
        writes_data=True,
        updates_rows="update" in kinds,
        deletes_rows="delete" in kinds,
    )


def _duplicates(names: list[str]) -> str | None:
    seen: set[str] = set()
    for name in names:
        if name.casefold() in seen:
            return name
        seen.add(name.casefold())
    return None


def validate_merge(statement: Statement, context: PlanningContext | None = None) -> None:
    """Checks that need no catalog metadata; the planner checks the rest."""
    assert isinstance(statement, MergeStatement)
    parsed = statement.parsed
    if any(
        type(node).__name__ == "StageReferenceContext"
        for node in walk_nodes(parsed.statement_context)
    ):
        raise SemanticError(
            "MERGE cannot read a @stage directly; load the files into a table first"
        )
    if redact_sql_credentials(parsed.normalized_sql) != parsed.normalized_sql:
        raise SemanticError("MERGE cannot carry credentials; read the data through a @stage")
    target_name = (statement.target_alias or statement.target[-1]).casefold()
    source_name = statement.source_alias or (
        statement.source_name[-1] if statement.source_name else None
    )
    if source_name is not None and source_name.casefold() == target_name:
        raise SemanticError(
            "MERGE target and source have the same name; give one of them an alias"
        )
    if statement.keys is None:
        raise SemanticError(
            "MERGE ON must compare the target's primary key columns with source values, "
            f"joined by AND ({_KEY_EXAMPLE}); put other conditions in WHEN ... AND"
        )
    kinds = [clause.kind for clause in statement.clauses]
    if "delete" in kinds and "insert" in kinds:
        raise SemanticError(
            "MERGE cannot combine WHEN MATCHED ... THEN DELETE with WHEN NOT MATCHED ... "
            "THEN INSERT; run the delete and the upsert as two MERGE statements"
        )
    for matched in (True, False):
        group = [
            clause for clause in statement.clauses if (clause.kind != "insert") is matched
        ]
        for clause in group[:-1]:
            if clause.condition is None:
                label = "WHEN MATCHED" if matched else "WHEN NOT MATCHED"
                raise SemanticError(
                    f"A {label} clause without AND <condition> must be the last {label} clause; "
                    "the clauses after it are never reached"
                )
    for clause in statement.clauses:
        if clause.kind == "update" and (
            column := _duplicates([name for name, _ in clause.assignments])
        ):
            raise SemanticError(f"MERGE UPDATE sets column {column} more than once")
        if clause.kind == "insert":
            if clause.reads_target:
                raise SemanticError(
                    "WHEN NOT MATCHED has no target row; its condition and values can "
                    "read only the source"
                )
            if clause.columns is not None:
                if column := _duplicates(list(clause.columns)):
                    raise SemanticError(f"MERGE INSERT lists column {column} more than once")
                if len(clause.columns) != len(clause.values):
                    raise SemanticError("MERGE INSERT has a different number of columns and values")


def _quote(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _same_sql(left: str, right: str) -> bool:
    def norm(text: str) -> str:
        return re.sub(r"\s+", "", text).replace("`", "").casefold()

    return norm(left) == norm(right)


def _default(column: BoundColumn) -> str:
    """The column's table default as SQL, for ``DEFAULT`` and unwritten insert columns."""
    if column.auto_increment:
        raise SemanticError(
            f"MERGE INSERT must give a value for auto-increment column {column.name} "
            "when another clause sets it"
        )
    if column.default_sql:
        return column.default_sql
    if column.default is None:
        if column.nullable is False:
            raise SemanticError(
                f"MERGE INSERT must give a value for {column.name}: it is NOT NULL "
                "and has no default"
            )
        return "NULL"
    if column.default.upper().startswith("CURRENT_TIMESTAMP"):
        return column.default
    return _literal(column.default)


def _generation_inputs(columns: tuple[BoundColumn, ...]) -> set[str]:
    """Columns a generated column reads: a partial update must write them too."""
    from antlr4 import CommonTokenStream, InputStream

    from app.sql_dialect.grammar import StarRocksLexer

    names = {column.name.casefold() for column in columns if not column.generated_expression}
    inputs: set[str] = set()
    for column in columns:
        if not column.generated_expression:
            continue
        stream = CommonTokenStream(StarRocksLexer(InputStream(column.generated_expression)))
        stream.fill()
        for token in stream.tokens:
            text = (token.text or "").strip("`").casefold()
            if text in names:
                inputs.add(text)
    return inputs


class _Lowering:
    """Builds the engine statements for one bound ``MERGE``."""

    def __init__(self, statement: MergeStatement, name: TableName, columns, keys) -> None:
        self.statement = statement
        self.columns: tuple[BoundColumn, ...] = columns
        self.keys: dict[str, str] = keys
        self.qualifier = (
            _quote(statement.target_alias) if statement.target_alias else statement.target_sql
        )
        self.source = statement.source_sql + (
            f" AS {_quote(statement.source_alias)}" if statement.source_alias else ""
        )
        self.target = statement.target_sql + (
            f" AS {_quote(statement.target_alias)}" if statement.target_alias else ""
        )
        self.label = ".".join(part for part in (name.database, name.table) if part)

    def column(self, column: BoundColumn) -> str:
        return f"{self.qualifier}.{_quote(column.name)}"

    def action(self) -> str:
        """The number of the clause that applies to a row, or NULL."""

        def first(matched: bool) -> str:
            branches = [
                f"WHEN {f'({clause.condition})' if clause.condition else 'TRUE'} THEN {number}"
                for number, clause in enumerate(self.statement.clauses, 1)
                if (clause.kind != "insert") is matched
            ]
            return f"CASE {' '.join(branches)} END" if branches else "NULL"

        key = next(column for column in self.columns if column.name.casefold() in self.keys)
        return (
            f"(CASE WHEN {self.column(key)} IS NOT NULL THEN {first(True)} "
            f"ELSE {first(False)} END)"
        )

    def numbers(self, *kinds: str) -> str:
        return ", ".join(
            str(number)
            for number, clause in enumerate(self.statement.clauses, 1)
            if clause.kind in kinds
        )

    def assertion(self) -> str:
        message = _literal(
            f"MERGE INTO {self.label}: more than one source row matches the same key"
        )
        partition = ", ".join(self.keys.values())
        return f"assert_true(COUNT(*) OVER (PARTITION BY {partition}) = 1, {message})"

    def upsert(self, values: dict[int, dict[str, str]]) -> str:
        """The upsert, writing only the key and the columns some clause sets.

        It is a partial update: a matched row keeps every column no clause
        writes, and a new row gets the table default for those columns. Columns
        the MERGE does not mention, masked ones included, are never rewritten.
        """
        action = self.action()
        written = {name for assigned in values.values() for name in assigned}
        written |= _generation_inputs(self.columns)
        # The engine's partial update resets a DEFAULT CURRENT_TIMESTAMP column
        # (native UPDATE does not), so such a column is always written: a
        # matched row keeps its value, a new row gets the default.
        written |= {
            column.name.casefold()
            for column in self.columns
            if (column.default_sql or column.default or "").upper().startswith("CURRENT_TIMESTAMP")
        }
        selected: list[str] = []
        names: list[str] = []
        guarded = False
        for column in self.columns:
            name = column.name.casefold()
            if column.generated_expression or (name not in self.keys and name not in written):
                continue
            branches = []
            for number, assigned in values.items():
                if name in assigned:
                    branches.append(f"WHEN {number} THEN {assigned[name]}")
                elif self.statement.clauses[number - 1].kind == "insert":
                    branches.append(f"WHEN {number} THEN {_default(column)}")
            value = (
                f"CASE {action} {' '.join(branches)} ELSE {self.column(column)} END"
                if branches
                else self.column(column)
            )
            if not guarded and name in self.keys:
                value = f"IF({self.assertion()}, {value}, NULL)"
                guarded = True
            names.append(_quote(column.name))
            selected.append(value)
        return (
            f"INSERT /*+ SET_VAR(enable_insert_partial_update=true) */ INTO "
            f"{self.statement.target_sql} ({', '.join(names)}) "
            f"SELECT {', '.join(selected)} FROM {self.source} LEFT JOIN {self.target} "
            f"ON {self.statement.on_sql} WHERE {action} IN ({self.numbers('update', 'insert')})"
        )

    def matched_keys(self, numbers: str) -> str:
        keys = [column for column in self.columns if column.name.casefold() in self.keys]
        return (
            f"SELECT {', '.join(self.column(column) for column in keys)} "
            f"FROM {self.source} JOIN {self.target} ON {self.statement.on_sql} "
            f"WHERE {self.action()} IN ({numbers})"
        )

    def delete(self) -> str:
        keys = [
            _quote(column.name)
            for column in self.columns
            if column.name.casefold() in self.keys
        ]
        tested = keys[0] if len(keys) == 1 else f"({', '.join(keys)})"
        return (
            f"DELETE FROM {self.statement.target_sql} WHERE {tested} IN "
            f"({self.matched_keys(self.numbers('delete'))})"
        )

    def check(self) -> str:
        message = _literal(
            f"MERGE INTO {self.label}: more than one source row matches the same key"
        )
        return (
            f"SELECT assert_true(COALESCE(MAX(`__nova_rows`), 1) = 1, {message}) AS `checked` "
            f"FROM (SELECT COUNT(*) AS `__nova_rows` FROM {self.source} JOIN {self.target} "
            f"ON {self.statement.on_sql} WHERE {self.action()} IS NOT NULL "
            f"GROUP BY {', '.join(self.keys.values())}) AS `__nova_merge_keys`"
        )


def _bind_values(statement: MergeStatement, columns, keys: dict[str, str]):
    """Each clause's value for each target column it writes, as SQL."""
    by_name = {column.name.casefold(): column for column in columns}
    writable = [column for column in columns if not column.generated_expression]
    values: dict[int, dict[str, str]] = {}
    for number, clause in enumerate(statement.clauses, 1):
        if clause.kind == "update":
            assigned: dict[str, str] = {}
            for name, value in clause.assignments:
                column = by_name.get(name.casefold())
                if column is None:
                    raise SemanticError(f"MERGE UPDATE names unknown column {name}")
                if column.generated_expression:
                    raise SemanticError(f"MERGE UPDATE cannot set generated column {name}")
                if name.casefold() in keys:
                    raise SemanticError(
                        f"MERGE UPDATE cannot change key column {name}; a matched row keeps its key"
                    )
                assigned[name.casefold()] = value if value is not None else _default(column)
            values[number] = assigned
        if clause.kind == "insert":
            names = (
                list(clause.columns)
                if clause.columns is not None
                else [column.name for column in writable]
            )
            if len(names) != len(clause.values):
                raise SemanticError(
                    f"MERGE INSERT gives {len(clause.values)} values for {len(names)} columns"
                )
            given: dict[str, str | None] = {}
            for name, value in zip(names, clause.values, strict=True):
                column = by_name.get(name.casefold())
                if column is None:
                    raise SemanticError(f"MERGE INSERT names unknown column {name}")
                if column.generated_expression:
                    raise SemanticError(f"MERGE INSERT cannot set generated column {name}")
                given[name.casefold()] = value
            # A column the clause omits is not written for its rows: the
            # partial-update insert gives it the table default (or the next
            # auto-increment value), unless another clause writes it.
            inserted: dict[str, str] = {}
            for column in writable:
                name = column.name.casefold()
                if name in keys:
                    value = given.get(name)
                    if value is not None and not _same_sql(value, keys[name]):
                        raise SemanticError(
                            f"MERGE INSERT must give key column {column.name} the value it is "
                            f"matched on ({keys[name]})"
                        )
                    inserted[name] = keys[name]
                elif name in given:
                    value = given[name]
                    inserted[name] = value if value is not None else _default(column)
            values[number] = inserted
    return values


class MergePlanner:
    async def plan(self, logical: LogicalPlan, context: PlanningContext) -> ExecutionPlan:
        statement = logical.statement
        assert isinstance(statement, MergeStatement) and statement.keys is not None
        if context.binder is None:
            raise SemanticError("MERGE needs catalog metadata")
        parts = statement.target
        if len(parts) > 3:
            raise SemanticError("MERGE target must be [catalog.]database.table")
        name = TableName(
            parts[-1],
            parts[-2] if len(parts) > 1 else context.database,
            catalog=parts[0] if len(parts) == 3 else INTERNAL_CATALOG,
        )
        if name.catalog != INTERNAL_CATALOG:
            raise SemanticError(
                f"MERGE writes Nova tables only; {name.catalog} is an external catalog. "
                "Read from it in USING instead"
            )
        if not name.database:
            raise SemanticError("MERGE needs a database: qualify the target or select one")
        table = await context.binder.get_details(name)
        if table.table_type != "BASE TABLE" or table.key_type != "PRIMARY":
            raise SemanticError(
                f"MERGE needs a Primary Key table; {name.database}.{name.table} is not one"
            )
        primary = {column.casefold() for column in table.primary_key_columns}
        keys: dict[str, str] = {}
        for column, source in statement.keys:
            if column.casefold() not in primary or column.casefold() in keys:
                raise SemanticError(
                    "MERGE ON must compare each primary key column "
                    f"({', '.join(table.primary_key_columns)}) once with a source value "
                    f"({_KEY_EXAMPLE}); {column} is not one of them"
                )
            keys[column.casefold()] = source
        if set(keys) != primary:
            missing = [
                column for column in table.primary_key_columns if column.casefold() not in keys
            ]
            raise SemanticError(
                f"MERGE ON must match on the whole primary key; {', '.join(missing)} is missing"
            )
        values = _bind_values(statement, table.columns, keys)
        lowering = _Lowering(statement, name, table.columns, keys)
        kinds = {clause.kind for clause in statement.clauses}
        steps: list[EngineSqlPlan] = []

        def step(sql: str, effects: PlanEffects) -> EngineSqlPlan:
            from app.sql_frontend.analysis.semantics import confirmation_policy

            return EngineSqlPlan(
                sql,
                sql,
                effects,
                logical.source_key,
                requires_confirmation=confirmation_policy(effects),
            )

        if "delete" in kinds:
            steps.append(step(lowering.check(), PlanEffects(reads_data=True)))
            steps.append(
                step(
                    lowering.delete(),
                    PlanEffects(reads_data=True, writes_data=True, deletes_rows=True),
                )
            )
        if kinds & {"update", "insert"}:
            steps.append(
                step(
                    lowering.upsert(values),
                    PlanEffects(
                        reads_data=True, writes_data=True, updates_rows="update" in kinds
                    ),
                )
            )
        if len(steps) == 1:
            return steps[0]
        return CompositePlan(tuple(steps), Atomicity.BEST_EFFORT)
