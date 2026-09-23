"""Compile a small, explicit subset of author guidance into planner constraints."""

from __future__ import annotations

import re

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlanError


class GuidanceRequiresPlanning(SemanticPlanError):
    pass


def required_named_filters(
    model: SemanticModelIR, *, allow_natural_language: bool = False
) -> tuple[str, ...]:
    text = model.query_generation_instructions.strip()
    if not text:
        return ()
    if len(text) > 4000:
        raise SemanticPlanError("Query guidance exceeds the supported size.")
    names = []
    for clause in re.split(r"[\n;]+", text):
        match = re.fullmatch(
            r"\s*always\s+(?:apply|use)\s+(?:the\s+)?named\s+filter\s+['\"`]([\w]+)['\"`]\.?\s*",
            clause,
            re.I,
        )
        if not match:
            if allow_natural_language:
                continue
            raise GuidanceRequiresPlanning(
                "Query guidance is not a supported named-filter rule. "
                "Represent the rule as a named filter before executing this request."
            )
        if model.named_filter(match[1]) is None:
            raise SemanticPlanError("Query guidance references an unknown named filter.")
        names.append(match[1])
    return tuple(dict.fromkeys(names))


def enforce_routing_guidance(
    model: SemanticModelIR, question: str, *, allow_natural_language: bool = False
) -> None:
    text = model.question_routing_instructions.strip()
    if not text:
        return
    if len(text) > 4000:
        raise SemanticPlanError("Routing guidance exceeds the supported size.")
    for clause in re.split(r"[\n;]+", text):
        match = re.fullmatch(
            r"\s*when\s+['\"`]([^'\"`]+)['\"`]\s+is\s+ambiguous\b[^;\n]*"
            r"\bask\s+(?:for\s+)?clarification\.?\s*",
            clause,
            re.I,
        )
        if not match:
            if allow_natural_language:
                continue
            raise GuidanceRequiresPlanning(
                "Routing guidance is not a supported clarification rule. "
                "Clarify the model guidance before executing this request."
            )
        term = re.escape(match[1].strip())
        if re.search(r"(?<!\w)" + term + r"(?!\w)", question, re.I):
            raise SemanticPlanError("The semantic model requires clarification for this term.")
