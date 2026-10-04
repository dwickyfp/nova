from __future__ import annotations

from collections.abc import Callable

from app.sql_dialect.grammar import StarRocksParser
from app.sql_frontend.antlr_utils import walk_nodes as _walk_nodes
from app.sql_frontend.ast.statements import (
    CreateMLModelStatement,
    CreateTaskStatement,
    ForcePasswordChangeStatement,
    MLForecastStatement,
    MLMaterializeStatement,
    MLPredictStatement,
    NativeStatement,
    SecurityStatement,
    StageAwareStatement,
    Statement,
)
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import ParsedStatement

_NATIVE_CONTEXTS = frozenset(
    name[0].upper() + name[1:] + "Context"
    for name in vars(StarRocksParser.StatementContext)
    if name in StarRocksParser.ruleNames
    and not name.startswith("nova")
    and name != "createMlModelStatement"
)


class AstBuilderRegistry:
    def __init__(self) -> None:
        self._builders: dict[str, Callable[[ParsedStatement], Statement]] = {}

    def register(self, context_name: str, builder: Callable[[ParsedStatement], Statement]) -> None:
        if context_name in self._builders:
            raise ValueError(f"AST builder already registered for {context_name}")
        self._builders[context_name] = builder

    def build(self, parsed: ParsedStatement) -> Statement:
        builder = self._builders.get(type(parsed.statement_context).__name__)
        if builder:
            return builder(parsed)
        if type(parsed.statement_context).__name__ not in _NATIVE_CONTEXTS:
            raise SemanticError("No AST builder registered for SQL extension")
        return _native_or_stage(parsed)


def _native_or_stage(parsed: ParsedStatement) -> Statement:
    if any(
        type(node).__name__ == "StageReferenceContext"
        for node in _walk_nodes(parsed.statement_context)
    ):
        return StageAwareStatement(parsed)
    return NativeStatement(parsed)


def _task(parsed: ParsedStatement) -> Statement:
    if parsed.visible_tokens[0].text.upper() == "CREATE":
        return CreateTaskStatement(parsed)
    return _native_or_stage(parsed)


def _query(parsed: ParsedStatement) -> Statement:
    from app.common.ml_intercept import detect_ml_predict, detect_ml_predict_table

    if parsed.visible_tokens[0].text.upper() in {"EXPLAIN", "DESC", "DESCRIBE"}:
        return _native_or_stage(parsed)
    if detect_ml_predict_table(parsed.normalized_sql, parsed=parsed):
        return MLMaterializeStatement(parsed)
    call = detect_ml_predict(parsed.normalized_sql, tree=parsed.parse_tree)
    if call:
        return MLPredictStatement(parsed, call)
    return _native_or_stage(parsed)


def default_builders() -> AstBuilderRegistry:
    registry = AstBuilderRegistry()
    for name, builder in {
        "CreateMlModelStatementContext": CreateMLModelStatement,
        "SubmitTaskStatementContext": _task,
        "QueryStatementContext": _query,
        "CreateInternalFunctionStmtContext": NativeStatement,
        "CreateUdfFunctionStmtContext": NativeStatement,
        "NovaPlanAdvisorStatementContext": NativeStatement,
        "NovaForecastStatementContext": MLForecastStatement,
        "NovaForcePasswordStatementContext": ForcePasswordChangeStatement,
        "NovaListStatementContext": StageAwareStatement,
        "NovaCopyStatementContext": StageAwareStatement,
        "NovaStageInsertStatementContext": StageAwareStatement,
        "NovaSecurityShowStatementContext": SecurityStatement,
        "CreateRoleStatementContext": SecurityStatement,
        "CreateUserStatementContext": SecurityStatement,
        "AlterUserStatementContext": SecurityStatement,
        "DropUserStatementContext": SecurityStatement,
        "SetDefaultRoleStatementContext": SecurityStatement,
        "DropRoleStatementContext": SecurityStatement,
        "ShowRolesStatementContext": SecurityStatement,
        "ShowGrantsStatementContext": SecurityStatement,
        "GrantRoleToUserContext": SecurityStatement,
        "GrantRoleToRoleContext": SecurityStatement,
        "GrantRoleToGroupContext": SecurityStatement,
        "RevokeRoleFromUserContext": SecurityStatement,
        "RevokeRoleFromRoleContext": SecurityStatement,
        "RevokeRoleFromGroupContext": SecurityStatement,
        "GrantOnUserContext": SecurityStatement,
        "GrantOnSystemContext": SecurityStatement,
        "GrantOnTableBriefContext": SecurityStatement,
        "GrantOnFuncContext": SecurityStatement,
        "GrantOnPrimaryObjContext": SecurityStatement,
        "GrantOnAllContext": SecurityStatement,
        "RevokeOnUserContext": SecurityStatement,
        "RevokeOnSystemContext": SecurityStatement,
        "RevokeOnTableBriefContext": SecurityStatement,
        "RevokeOnFuncContext": SecurityStatement,
        "RevokeOnPrimaryObjContext": SecurityStatement,
        "RevokeOnAllContext": SecurityStatement,
    }.items():
        registry.register(name, builder)
    return registry


ast_builders = default_builders()
