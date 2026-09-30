from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SyntaxDiagnostic:
    line: int
    column: int
    message: str

    def __str__(self) -> str:
        return f"{self.line}:{self.column} {self.message}"


class SQLFrontendError(ValueError):
    pass


class SQLSyntaxError(SQLFrontendError):
    def __init__(self, diagnostics: tuple[SyntaxDiagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__("Invalid SQL: " + str(diagnostics[0]))


class SemanticError(SQLFrontendError):
    pass
