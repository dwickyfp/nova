"""Compile derived metric arithmetic from already validated base metrics."""

from __future__ import annotations

import re

from app.modules.agents.semantic.expressions import parse_expression, qualify_expression
from app.modules.agents.semantic.ir import SemanticMetricIR, SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlanError

_ARITHMETIC = re.compile(r"^[A-Za-z0-9_+\-*/().\s]+$")
_TOKEN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_NUMERIC_TYPES = {"integer", "decimal", "float", "double", "number", "numeric"}
_OPEN, _CLOSE = "\x01", "\x02"
_OPERAND = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*|[0-9.]+)")


def _guard_divisors(expression: str) -> str:
    """Mark every divisor so a zero denominator yields NULL, not an error or infinity.

    A divisor is a name, a number or a parenthesised group; it is wrapped in
    sentinels that become ``NULLIF(divisor, 0)`` once names are substituted.
    """
    out: list[str] = []
    index = 0
    while index < len(expression):
        char = expression[index]
        out.append(char)
        index += 1
        if char != "/":
            continue
        out.append(" ")
        while index < len(expression) and expression[index].isspace():
            index += 1
        if index < len(expression) and expression[index] == "(":
            depth, end = 0, index
            while end < len(expression):
                depth += {"(": 1, ")": -1}.get(expression[end], 0)
                end += 1
                if depth == 0:
                    break
            if depth:
                raise SemanticPlanError("Unbalanced parentheses in derived metric")
            inner = _guard_divisors(expression[index:end])
            out.append(f"{_OPEN}{inner}{_CLOSE}")
            index = end
            continue
        match = _OPERAND.match(expression, index)
        if match is None:
            raise SemanticPlanError("Derived metric division needs a divisor")
        out.append(f"{_OPEN}{match.group(1)}{_CLOSE}")
        index = match.end()
    return "".join(out)


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

    expression = _TOKEN.sub(replace, _guard_divisors(metric.expression))
    expression = expression.replace(_OPEN, "NULLIF(").replace(_CLOSE, ", 0)")
    if used != set(dependencies):
        raise SemanticPlanError("Derived metric dependency is not used in its expression")
    parse_expression(expression)
    return f"({expression})"
