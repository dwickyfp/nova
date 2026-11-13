from __future__ import annotations

from dataclasses import dataclass

from app.core.exceptions import ForbiddenSQLError
from app.sql_frontend.analysis.effects import PlanEffects


@dataclass(frozen=True, slots=True)
class SyntaxDiagnostic:
    line: int
    column: int
    message: str

    def __str__(self) -> str:
        return f"{self.line}:{self.column} {self.message}"


class SQLFrontendError(ValueError):
    code = "semantic_error"


class SQLSyntaxError(SQLFrontendError):
    code = "syntax_error"

    def __init__(self, diagnostics: tuple[SyntaxDiagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__("Invalid SQL: " + str(diagnostics[0]))


class SemanticError(SQLFrontendError):
    pass


class BindingError(SemanticError):
    code = "binding_error"


class StageReferenceError(BindingError):
    """A ``@stage`` reference names no stage, or more than one, in scope."""

    def __init__(self, message: str, code: str = "stage_not_found") -> None:
        super().__init__(message)
        self.code = code


class CapabilityUnsupportedError(SemanticError):
    code = "capability_unsupported"


class ConfirmationRequiredError(ForbiddenSQLError):
    code = "confirmation_required"

    def __init__(self, effects: PlanEffects, statement_kind: str = "statement") -> None:
        self.effects = effects
        self.statement_kind = statement_kind
        super().__init__("Destructive SQL requires confirmation before execution.")
