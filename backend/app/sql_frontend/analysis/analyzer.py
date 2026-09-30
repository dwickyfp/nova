from __future__ import annotations

from dataclasses import dataclass

from app.modules.query.dialect.parser import _walk_nodes
from app.sql_frontend.analysis.effects import PlanEffects
from app.sql_frontend.ast.statements import (
    CreateMLModelStatement,
    MLMaterializeStatement,
    Statement,
)


@dataclass(frozen=True, slots=True)
class Analysis:
    effects: PlanEffects
    requires_confirmation: bool = False


def analyze(statement: Statement) -> Analysis:
    parsed = statement.parsed
    tokens = [token.text.upper() for token in parsed.visible_tokens]
    root = type(parsed.statement_context).__name__
    nodes = {type(node).__name__ for node in _walk_nodes(parsed.statement_context)}
    lead = tokens[0]
    security = root.startswith(("Grant", "Revoke")) or lead in {"GRANT", "REVOKE"}
    security |= lead in {"CREATE", "ALTER", "DROP", "SET"} and any(
        word in tokens[1:3] for word in {"ROLE", "USER", "PASSWORD"}
    )
    schema = lead in {"CREATE", "ALTER", "DROP", "TRUNCATE", "RECOVER"} and not security
    writes = lead in {"INSERT", "UPDATE", "DELETE", "TRUNCATE", "LOAD"}
    writes |= "CreateTableAsSelectStatementContext" in nodes
    reads = lead in {"SELECT", "WITH", "SHOW", "DESC", "DESCRIBE", "EXPLAIN"}
    reads |= "QueryStatementContext" in nodes
    external = "FileTableFunctionContext" in nodes or "OutfileContext" in nodes
    external |= "StageReferenceContext" in nodes
    if lead == "COPY":
        reads, writes, external = True, True, True
    if lead == "EXPLAIN":
        return Analysis(PlanEffects(reads_data=True, external_io=external))
    # Task bodies describe future work and do not execute when metadata is created.
    if root == "SubmitTaskStatementContext" and lead == "CREATE":
        reads, writes = False, True
    if isinstance(statement, (CreateMLModelStatement, MLMaterializeStatement)):
        writes = True
    requires = lead in {"UPDATE", "DELETE", "DROP", "TRUNCATE"}
    requires |= (
        lead == "ALTER"
        and "TABLE" in tokens[1:3]
        and any(name.startswith("Drop") for name in nodes)
    )
    return Analysis(
        PlanEffects(reads, writes, lead in {"DELETE", "TRUNCATE"}, schema, security, external),
        requires,
    )
