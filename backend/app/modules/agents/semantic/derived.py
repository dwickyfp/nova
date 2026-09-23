"""Compile derived metric arithmetic from already validated base metrics."""

from __future__ import annotations

import re

from app.modules.agents.semantic.expressions import parse_expression, qualify_expression
from app.modules.agents.semantic.ir import SemanticMetricIR, SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlanError

_ARITHMETIC = re.compile(r"^[A-Za-z0-9_+\-*/().\s]+$")
_TOKEN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_NUMERIC_TYPES = {"integer", "decimal", "float", "double", "number", "numeric"}


def compile_metric_expression(
    model: SemanticModelIR,
    metric: SemanticMetricIR,
    stack: tuple[str, ...] = (),
) -> str:
    if metric.name in stack:
        raise SemanticPlanError("Derived metric dependency cycle")
    if not metric.dependencies:
        return qualify_expression(metric.expression, metric.base_dataset, model)
    if metric.datatype and metric.datatype.lower() not in _NUMERIC_TYPES:
        raise SemanticPlanError("Derived metric output must have a numeric datatype")
    if not _ARITHMETIC.fullmatch(metric.expression):
        raise SemanticPlanError("Derived metrics support arithmetic over named metrics only")
    dependencies = {}
    for name in metric.dependencies:
        child = model.metric(name)
        if child is None:
            raise SemanticPlanError(f"Unknown derived metric dependency {name!r}")
        if child.base_dataset != metric.base_dataset:
            raise SemanticPlanError("Derived metrics must share a base dataset")
        if child.datatype and child.datatype.lower() not in _NUMERIC_TYPES:
            raise SemanticPlanError(f"Derived metric dependency {name!r} is not numeric")
        dependencies[name] = compile_metric_expression(model, child, (*stack, metric.name))
    used = set()

    def replace(match: re.Match[str]) -> str:
        name = match.group()
        if name not in dependencies:
            raise SemanticPlanError(f"Unknown identifier in derived metric: {name!r}")
        used.add(name)
        return f"({dependencies[name]})"

    expression = _TOKEN.sub(replace, metric.expression)
    if used != set(dependencies):
        raise SemanticPlanError("Derived metric dependency is not used in its expression")
    parse_expression(expression)
    return f"({expression})"
