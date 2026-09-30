from dataclasses import dataclass, field

from app.sql_frontend.analysis.analyzer import Analysis
from app.sql_frontend.ast.statements import Statement


@dataclass(frozen=True, slots=True)
class LogicalPlan:
    statement: Statement = field(repr=False)
    analysis: Analysis
    source_key: int = 0
