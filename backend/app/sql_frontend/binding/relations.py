from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from app.sql_frontend.antlr_utils import walk_nodes
from app.sql_frontend.binding.catalog import Binder
from app.sql_frontend.binding.models import BoundOutputColumn, BoundRelation, TableName
from app.sql_frontend.binding.types import UnknownType
from app.sql_frontend.errors import BindingError
from app.sql_frontend.parser import ParsedStatement, parse_statement
from app.sql_frontend.stage_parser import StageReference, stage_reference_from_atom


def identifier(node: Any) -> str:
    text = node.getText()
    return text[1:-1].replace("``", "`") if text.startswith("`") else text


def table_name(node: Any, database: str | None = None) -> TableName:
    parts = [identifier(item) for item in node.identifier()]
    if len(parts) == 1:
        return TableName(parts[0], database)
    if len(parts) == 2:
        return TableName(parts[1], parts[0])
    if len(parts) == 3:
        return TableName(parts[2], parts[1], catalog=parts[0])
    raise BindingError("Unsupported table identity")


@dataclass(frozen=True, slots=True)
class StageRelation:
    reference: StageReference


StageSchemaProvider = Callable[[StageReference], Awaitable[BoundRelation]]


class RelationBinder:
    def __init__(
        self,
        binder: Binder,
        *,
        database: str | None = None,
        stage_schema: StageSchemaProvider | None = None,
    ) -> None:
        self.binder = binder
        self.database = database
        self.stage_schema = stage_schema
        self._cache: dict[str, BoundRelation] = {}

    async def bind_relation(
        self, relation: str | TableName | ParsedStatement | StageRelation
    ) -> BoundRelation:
        if isinstance(relation, TableName):
            return await self._table(relation)
        if isinstance(relation, StageRelation):
            return await self._stage(relation.reference)
        parsed = parse_statement(relation) if isinstance(relation, str) else relation
        if type(parsed.statement_context).__name__ != "QueryStatementContext":
            raise BindingError("Relation binding requires a SELECT query")
        key = parsed.normalized_sql
        if key not in self._cache:
            root = parsed.statement_context.queryRelation()
            self._cache[key] = await self._query(root, {}, parsed, 0)
        return self._cache[key]

    async def _table(self, name: TableName) -> BoundRelation:
        columns = await self.binder.get_columns(name)
        return BoundRelation(
            tuple(
                BoundOutputColumn(column.name, column.sql_type, column.nullable)
                for column in columns
            )
        )

    async def _stage(self, reference: StageReference) -> BoundRelation:
        if self.stage_schema is None:
            raise BindingError("Stage schema binding is unavailable")
        key = "stage:" + reference.full_match
        if key not in self._cache:
            self._cache[key] = await self.stage_schema(reference)
        return self._cache[key]

    async def _query(
        self, node: Any, ctes: dict[str, BoundRelation], parsed: ParsedStatement, depth: int
    ) -> BoundRelation:
        if depth > 32:
            raise BindingError("Relation nesting exceeds limit")
        local = dict(ctes)
        with_clause = node.withClause()
        if with_clause:
            if any(token.getText().upper() == "RECURSIVE" for token in with_clause.children or []):
                raise BindingError("Recursive relation binding is unsupported")
            for cte in with_clause.commonTableExpression():
                name = identifier(cte.identifier()).casefold()
                relation = await self._query(cte.queryRelation(), local, parsed, depth + 1)
                aliases = cte.columnAliases()
                if aliases:
                    names = [identifier(item) for item in aliases.identifier()]
                    if len(names) != len(relation.columns):
                        raise BindingError("CTE alias count does not match output")
                    relation = BoundRelation(
                        tuple(
                            replace(column, name=name)
                            for column, name in zip(relation.columns, names, strict=True)
                        )
                    )
                if name in local:
                    raise BindingError("Duplicate CTE binding")
                local[name] = relation
        primary = node.queryNoWith().queryPrimary()
        while type(primary).__name__ not in {"QuerySpecificationContext", "QueryRelationContext"}:
            contexts = [c for c in primary.children or [] if type(c).__name__.endswith("Context")]
            if len(contexts) != 1:
                raise BindingError("Unsupported query output form")
            primary = contexts[0]
        if type(primary).__name__ == "QueryRelationContext":
            return await self._query(primary, local, parsed, depth + 1)
        sources: list[tuple[str, BoundRelation]] = []
        from_node = next(
            (child for child in primary.children or [] if type(child).__name__ == "FromContext"),
            None,
        )
        if from_node and from_node.relations():
            if from_node.pivotClause():
                raise BindingError("Pivot output binding is unsupported")
            for relation in from_node.relations().relation():
                sources.append(await self._source(relation.relationPrimary(), local, parsed, depth))
                for join in relation.joinRelation():
                    criteria = join.joinCriteria()
                    if criteria and criteria.identifier():
                        raise BindingError("USING join output binding is unsupported")
                    joined = await self._source(join.relationPrimary(), local, parsed, depth)
                    join_type = join.outerAndSemiJoinType() or join.asofJoinType()
                    modifiers = (
                        {child.getText().upper() for child in join_type.children or []}
                        if join_type
                        else set()
                    )
                    if modifiers & {"SEMI", "ANTI"}:
                        raise BindingError("Semi/anti join output binding is unsupported")
                    if modifiers & {"RIGHT", "FULL"}:
                        sources = [(alias, self._nullable(source)) for alias, source in sources]
                    if modifiers & {"LEFT", "FULL"}:
                        joined = (joined[0], self._nullable(joined[1]))
                    sources.append(joined)
        aliases = [name for name, _ in sources]
        if len(set(aliases)) != len(aliases):
            raise BindingError("Duplicate relation alias")
        output: list[BoundOutputColumn] = []
        for item in primary.selectItem():
            if type(item).__name__ == "SelectAllContext":
                qualifier = item.qualifiedName()
                selected = sources
                if qualifier:
                    name = identifier(qualifier.identifier()[-1]).casefold()
                    selected = [source for source in sources if source[0] == name]
                    if not selected:
                        raise BindingError("Wildcard qualifier is not bound")
                if not selected:
                    raise BindingError("Wildcard has no bound relation")
                output.extend(column for _, source in selected for column in source.columns)
                continue
            expression = item.expression()
            raw = parsed.normalized_sql[expression.start.start : expression.stop.stop + 1]
            tokens = [
                token
                for token in parsed.visible_tokens
                if expression.start.start <= token.start <= expression.stop.stop
            ]
            column_nodes = [
                n for n in walk_nodes(expression) if type(n).__name__ == "ColumnReferenceContext"
            ]
            simple = (
                len(column_nodes) == 1
                and all(
                    token.text == "."
                    or token.text.startswith("`")
                    or token.text.replace("_", "a").isalnum()
                    for token in tokens
                )
                and len(tokens) % 2 == 1
                and all(tokens[i].text == "." for i in range(1, len(tokens), 2))
            )
            bound = None
            if simple:
                names = [token.text.strip("`").replace("``", "`") for token in tokens[::2]]
                selected = (
                    sources
                    if len(names) == 1
                    else [s for s in sources if s[0] == names[-2].casefold()]
                )
                matches = [
                    c
                    for _, source in selected
                    for c in source.columns
                    if c.name.casefold() == names[-1].casefold()
                ]
                if len(matches) != 1:
                    raise BindingError("Column reference is missing or ambiguous")
                bound = matches[0]
            alias = item.identifier()
            if alias:
                name = identifier(alias)
            elif bound:
                name = bound.name
            else:
                raise BindingError("Computed relation output requires an alias")
            output.append(
                BoundOutputColumn(
                    name,
                    bound.sql_type if bound else UnknownType(),
                    bound.nullable if bound else None,
                    raw,
                )
            )
        return BoundRelation(tuple(output))

    @staticmethod
    def _nullable(relation: BoundRelation) -> BoundRelation:
        return BoundRelation(tuple(replace(column, nullable=True) for column in relation.columns))

    async def _source(
        self, node: Any, ctes: dict[str, BoundRelation], parsed: ParsedStatement, depth: int
    ) -> tuple[str, BoundRelation]:
        kind = type(node).__name__
        if kind == "TableAtomContext":
            name = table_name(node.qualifiedName(), self.database)
            alias = (
                identifier(node.identifier()).casefold()
                if node.identifier()
                else name.table.casefold()
            )
            source = (
                ctes.get(name.table.casefold())
                if len(node.qualifiedName().identifier()) == 1
                else None
            )
            return alias, source if source is not None else await self._table(name)
        if kind == "SubqueryWithAliasContext":
            alias = identifier(node.identifier()).casefold()
            return alias, await self._query(
                node.subquery().queryRelation(), ctes, parsed, depth + 1
            )
        if kind == "StageAtomContext":
            reference = stage_reference_from_atom(node, parsed.normalized_sql)
            alias = (
                identifier(node.identifier()).casefold()
                if node.identifier()
                else reference.stage_name.casefold()
            )
            return alias, await self._stage(reference)
        raise BindingError("Unsupported source relation")
