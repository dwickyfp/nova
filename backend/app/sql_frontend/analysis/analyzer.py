from __future__ import annotations

from dataclasses import dataclass

from app.sql_frontend.analysis.effects import PlanEffects
from app.sql_frontend.ast.statements import Statement


@dataclass(frozen=True, slots=True)
class Analysis:
    effects: PlanEffects
    requires_confirmation: bool = False
    statement_kind: str = "statement"


def analyze(statement: Statement) -> Analysis:
    from app.sql_frontend.analysis.semantics import semantics_registry

    return semantics_registry.analyze(statement)
