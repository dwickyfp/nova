"""Whether one SQL statement only reads, decided from the grammar frontend.

Read-only callers (assistant tools, semantic views, agent artifacts and the
MySQL proxy's ``READ ONLY`` transactions) ask this module instead of matching
keywords. The statement is parsed with the central grammar, built into a typed
AST and analyzed by the same semantics registry the planner uses, so a
``WITH ... DELETE`` is a delete however its CTE bodies are quoted or commented.

A statement is read-only when its effects read data and do nothing else. A read
of external files (``external_io`` without ``exports_data``) still only reads;
which files may be read is decided by the frontend's own validation (stages,
``FILES()`` rules), not here. Anything that does not parse, build or analyze is
not read-only: the check fails closed.
"""

from __future__ import annotations

from functools import lru_cache

from app.sql_frontend.analysis.effects import PlanEffects


@lru_cache(maxsize=1)
def _planner():
    from app.sql_frontend.planning.planner import SQLPlanner

    return SQLPlanner()


def reads_only(effects: PlanEffects) -> bool:
    """True when ``effects`` read data and change nothing."""
    return effects.reads_data and not effects.mutates


def statement_effects(sql: str) -> PlanEffects | None:
    """The preflight effects of one statement, or ``None`` when it cannot be analyzed.

    ``None`` covers a syntax error, an unsupported or refused statement and any
    failure while building or analyzing it: a classifier that cannot see a
    statement must not call it read-only.
    """
    from app.sql_frontend.ast.builder import ast_builders
    from app.sql_frontend.parser import parse_statement

    text = sql.strip().rstrip(";").strip()
    if not text:
        return None
    try:
        analysis = _planner().preflight(ast_builders.build(parse_statement(text)))
    except Exception:  # noqa: BLE001 - every failure is "not read-only"
        return None
    return analysis.effects


def is_read_only_statement(sql: str) -> bool:
    """True when the grammar frontend classifies ``sql`` as a pure read."""
    effects = statement_effects(sql)
    return effects is not None and reads_only(effects)


__all__ = ["is_read_only_statement", "reads_only", "statement_effects"]
