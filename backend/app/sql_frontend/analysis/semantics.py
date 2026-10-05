from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import partial
from typing import Any

from app.sql_frontend.analysis.effects import PlanEffects
from app.sql_frontend.antlr_utils import walk_nodes
from app.sql_frontend.ast import statements as ast
from app.sql_frontend.errors import SemanticError

_VALIDATIONS: ContextVar[dict | None] = ContextVar("sql_semantic_validations", default=None)


@contextmanager
def semantic_scope():
    token = _VALIDATIONS.set({})
    try:
        yield
    finally:
        _VALIDATIONS.reset(token)


def confirmation_policy(effects: PlanEffects) -> bool:
    return effects.deletes_rows or effects.updates_rows or effects.drops_objects


def _constant_effects(effects: PlanEffects, statement: ast.Statement) -> PlanEffects:
    return effects


@dataclass(frozen=True, slots=True)
class StatementSemantics:
    effects_resolver: Callable[[Any], PlanEffects]
    analyzer: Callable[[ast.Statement], Any] = lambda statement: statement
    validator: Callable[[ast.Statement, Any], Any] | None = None
    confirmation: Callable[[PlanEffects], bool] = confirmation_policy
    preflight_safe: bool = False


class StatementSemanticsRegistry:
    def __init__(self) -> None:
        self._entries: dict[type[ast.Statement], StatementSemantics] = {}

    def register(self, statement_type: type[ast.Statement], semantics: StatementSemantics) -> None:
        if statement_type in self._entries:
            raise ValueError("Statement semantics already registered")
        self._entries[statement_type] = semantics

    def resolve(self, statement: ast.Statement) -> StatementSemantics:
        try:
            return self._entries[type(statement)]
        except KeyError:
            raise SemanticError("No semantics registered for statement type") from None

    def analyze(self, statement: ast.Statement):
        from app.sql_frontend.analysis.analyzer import Analysis

        semantics = self.resolve(statement)
        effects = semantics.effects_resolver(semantics.analyzer(statement))
        return Analysis(effects, semantics.confirmation(effects), type(statement).__name__)

    def validate(self, statement: ast.Statement, context: Any) -> Any:
        if context is None:
            from app.sql_frontend.context import PlanningContext

            context = PlanningContext()
        semantics = self.resolve(statement)
        if semantics.validator is None:
            return None
        cache = _VALIDATIONS.get() if semantics.preflight_safe else None
        key = (
            id(self),
            type(statement),
            statement.parsed.normalized_sql,
            context.database,
            context.schema,
            context.ranger_enabled,
        )
        if cache is not None and key in cache:
            return cache[key]
        value = semantics.validator(statement, context)
        if cache is not None:
            cache[key] = value
        return value

    def validate_preflight(self, statement, context):
        if self.resolve(statement).preflight_safe:
            self.validate(statement, context)


_NATIVE_EFFECTS: dict[str, PlanEffects] = {}


def _native_effects(statement: ast.Statement) -> PlanEffects:
    root = type(statement.parsed.statement_context).__name__
    nodes = {type(node).__name__ for node in walk_nodes(statement.parsed.statement_context)}
    if root == "NovaPlanAdvisorStatementContext":
        return PlanEffects(
            reads_data=True, writes_metadata=statement.parsed.statement_context.ALTER() is not None
        )
    if "ExplainDescContext" in nodes:
        return PlanEffects(reads_data=True)
    effects = _NATIVE_EFFECTS.get(root, PlanEffects())
    effects |= PlanEffects(
        reads_data="QueryStatementContext" in nodes,
        external_io=bool(
            nodes & {"StageReferenceContext", "FileTableFunctionContext", "OutfileContext"}
        ),
    )
    if root == "InsertStatementContext":
        effects |= PlanEffects(
            replaces_data=any(
                token.text.upper() == "OVERWRITE" for token in statement.parsed.visible_tokens[:3]
            )
        )
    if root == "AlterTableStatementContext":
        effects |= PlanEffects(drops_objects=any(name.startswith("Drop") for name in nodes))
    if root == "NovaCopyStatementContext":
        effects |= PlanEffects(writes_data=True)
    return effects


def _security_effects(statement: ast.Statement) -> PlanEffects:
    kind = type(statement.parsed.statement_context).__name__
    if kind.startswith(("Show", "NovaSecurityShow")):
        return PlanEffects(reads_data=True)
    return PlanEffects(changes_security=True, drops_objects=kind.startswith("Drop"))


def default_semantics() -> StatementSemanticsRegistry:
    from app.sql_dialect.grammar import StarRocksParser
    from app.sql_frontend.analysis import validation

    for name in vars(StarRocksParser.StatementContext):
        context = name[0].upper() + name[1:] + "Context" if name else ""
        effects = PlanEffects()
        if name.startswith(("show", "desc", "explain")):
            effects = PlanEffects(reads_data=True)
        if name.startswith(("create", "alter", "drop", "recover")):
            effects = PlanEffects(changes_schema=True, drops_objects=name.startswith("drop"))
        if name.startswith(("grant", "revoke")) or any(
            name == operation + subject
            for operation in ("create", "alter", "drop", "setDefault")
            for subject in ("RoleStatement", "UserStatement")
        ):
            effects = PlanEffects(changes_security=True, drops_objects=name.startswith("drop"))
        _NATIVE_EFFECTS[context] = effects
    _NATIVE_EFFECTS.update(
        {
            "CreateInternalFunctionStmtContext": PlanEffects(changes_schema=True),
            "CreateUdfFunctionStmtContext": PlanEffects(changes_schema=True),
            "InsertStatementContext": PlanEffects(writes_data=True),
            "UpdateStatementContext": PlanEffects(
                writes_data=True, updates_rows=True, reads_data=True
            ),
            "DeleteStatementContext": PlanEffects(
                writes_data=True, deletes_rows=True, reads_data=True
            ),
            "TruncateTableStatementContext": PlanEffects(writes_data=True, deletes_rows=True),
            "CreateTableAsSelectStatementContext": PlanEffects(
                reads_data=True, writes_data=True, changes_schema=True
            ),
            "AnalyzeStatementContext": PlanEffects(reads_data=True, writes_metadata=True),
            "AnalyzeHistogramStatementContext": PlanEffects(reads_data=True, writes_metadata=True),
            "DropHistogramStatementContext": PlanEffects(writes_metadata=True),
            "RefreshMaterializedViewStatementContext": PlanEffects(
                reads_data=True, writes_data=True
            ),
            "LoadStatementContext": PlanEffects(writes_data=True, external_io=True),
            "SubmitTaskStatementContext": PlanEffects(reads_data=True, writes_data=True),
            "NovaStageInsertStatementContext": PlanEffects(reads_data=True, external_io=True),
            "NovaListStatementContext": PlanEffects(reads_data=True, external_io=True),
        }
    )
    registry = StatementSemanticsRegistry()
    from app.sql_frontend.streams import stream_effects, validate_stream

    registry.register(
        ast.StreamStatement,
        StatementSemantics(stream_effects, validator=validate_stream, preflight_safe=True),
    )
    native = StatementSemantics(
        _native_effects, validator=validation._native_session_settings, preflight_safe=True
    )
    registry.register(ast.NativeStatement, native)
    registry.register(ast.StageAwareStatement, native)
    registry.register(
        ast.SecurityStatement,
        StatementSemantics(
            _security_effects, validator=validation._security_context, preflight_safe=True
        ),
    )
    for node, effects, validator in (
        (ast.CreateTaskStatement, PlanEffects(writes_metadata=True), validation._task),
        (
            ast.CreateMLModelStatement,
            PlanEffects(reads_data=True, writes_metadata=True),
            validation._model_context,
        ),
        (ast.MLPredictStatement, PlanEffects(reads_data=True), validation._predict_context),
        (
            ast.MLMaterializeStatement,
            PlanEffects(reads_data=True, writes_data=True, changes_schema=True),
            validation._materialize_context,
        ),
        (ast.MLForecastStatement, PlanEffects(reads_data=True), validation._forecast_context),
        (
            ast.ForcePasswordChangeStatement,
            PlanEffects(writes_metadata=True, changes_security=True),
            validation._password_context,
        ),
    ):
        registry.register(
            node,
            StatementSemantics(
                partial(_constant_effects, effects), validator=validator, preflight_safe=True
            ),
        )
    return registry


semantics_registry = default_semantics()
