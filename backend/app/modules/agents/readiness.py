"""Agent readiness: what a builder should fix before users rely on a Studio agent.

Every check reads configuration and semantic metadata only; none queries data.
The sample-question check runs the deterministic planner (the same fast path a
live turn uses) over the agent's own sample questions, so a builder sees which
questions resolve without the model, which need it, and which the catalog
cannot express.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

Status = Literal["ok", "warn", "fail"]

_KEY_NAME = re.compile(r"(?:^id$|_id$|_key$|_code$|_uuid$)", re.I)
_TEXTUAL = {None, "", "string", "text", "varchar", "char"}


@dataclass(frozen=True)
class Check:
    id: str
    status: Status
    title: str
    detail: str = ""
    fix: str = ""


def _looks_categorical(field: Any) -> bool:
    return (
        field.kind.value == "fact"
        and not field.is_time
        and not _KEY_NAME.search(field.name)
        and (field.datatype or "").casefold() in _TEXTUAL
    )


def assess(agent: dict[str, Any], models: list[dict[str, Any]]) -> dict[str, Any]:
    from app.modules.agents.semantic.planning import SemanticPlanError, SemanticPlanner
    from app.modules.agents.semantic.runtime import lint_semantic_model

    checks: list[Check] = []
    irs = [model["_scoped_ir"] for model in models if model.get("_scoped_ir") is not None]
    tools = set(agent.get("default_tools") or [])
    if not irs:
        checks.append(Check(
            "semantic_view", "fail", "A published Semantic View is bound and readable",
            "No bound Semantic View is readable with your current role.",
            "Bind a published Semantic View in the agent settings, or check its access.",
        ))
    else:
        checks.append(Check(
            "semantic_view", "ok", "A published Semantic View is bound and readable",
            f"{len(irs)} view(s) readable.",
        ))
    for ir in irs:
        errors = [item for item in lint_semantic_model(ir) if item.severity == "error"]
        warnings = [item for item in lint_semantic_model(ir) if item.severity != "error"]
        checks.append(Check(
            f"lint:{ir.name}", "fail" if errors else ("warn" if warnings else "ok"),
            f"{ir.name}: model lint",
            "; ".join(item.message for item in (errors or warnings)[:5]),
            "Fix these in the Semantic View definition." if errors or warnings else "",
        ))
        categorical = [field.name for dataset in ir.datasets for field in dataset.fields
                       if _looks_categorical(field)]
        checks.append(Check(
            f"dimensions:{ir.name}", "warn" if categorical else "ok",
            f"{ir.name}: text fields usable for grouping",
            ("These text fields cannot be used to group or filter: "
             + ", ".join(categorical[:12])) if categorical else "",
            "Mark them as dimensions (add `dimension:` with a few sample values)."
            if categorical else "",
        ))
        dimensions = [field for dataset in ir.datasets for field in dataset.fields
                      if field.kind.value == "dimension" and not field.is_time]
        unnamed = [item.name for item in ir.metrics if not item.synonyms]
        checks.append(Check(
            f"synonyms:{ir.name}", "warn" if unnamed else "ok",
            f"{ir.name}: business names for metrics",
            ("Metrics without synonyms: " + ", ".join(unnamed[:12])) if unnamed else "",
            "Add the words users say, in each language they use (e.g. 'omzet', 'penjualan')."
            if unnamed else "",
        ))
        valueless = [field.name for field in dimensions if not field.sample_values]
        checks.append(Check(
            f"values:{ir.name}", "warn" if valueless else "ok",
            f"{ir.name}: sample values for filters",
            ("Dimensions without sample values: " + ", ".join(valueless[:12]))
            if valueless else "",
            "Add sample values so questions like 'in Jakarta' resolve to a filter."
            if valueless else "",
        ))
        timeless = [item.name for item in ir.metrics if not item.default_time_dimension]
        checks.append(Check(
            f"time:{ir.name}", "warn" if timeless else "ok",
            f"{ir.name}: a default time dimension per metric",
            ("Metrics without one: " + ", ".join(timeless[:12])) if timeless else "",
            "Set default_time_dimension so 'last month' filters the right date."
            if timeless else "",
        ))
    questions = [str(item) for item in agent.get("sample_questions") or []][:20]
    if questions and irs:
        planner = SemanticPlanner()
        outcome = {"fast": 0, "model": 0}
        for question in questions:
            resolved = False
            for ir in irs:
                try:
                    planned = planner.plan(ir, question)
                except SemanticPlanError:
                    continue
                if planned.plan is not None and not planned.confidence.unresolved:
                    resolved = True
                    break
            outcome["fast" if resolved else "model"] += 1
        checks.append(Check(
            "sample_questions", "ok" if outcome["fast"] else "warn",
            "Sample questions resolve against the catalog",
            f"{outcome['fast']} of {len(questions)} resolve without the model; "
            f"{outcome['model']} need the model planner or are not covered.",
            "Add synonyms or sample values for the words those questions use."
            if outcome["model"] else "",
        ))
    elif not questions:
        checks.append(Check("sample_questions", "warn", "Sample questions are set",
                            "Users see no starter questions.",
                            "Add three to five questions this agent should answer."))
    analysis = {"compute_metrics", "data_to_chart"} - tools
    has_data_tool = bool(tools & {"semantic_query", "semantic_view_query"})
    checks.append(Check(
        "tools", "ok" if has_data_tool else "fail", "A governed data tool is enabled",
        "" if has_data_tool else "Neither semantic_query nor semantic_view_query is enabled.",
        "" if has_data_tool else "Enable semantic_query.",
    ))
    if has_data_tool and "data_to_chart" in analysis:
        checks.append(Check("charts", "warn", "Charts are enabled",
                            "The agent cannot draw charts.", "Enable data_to_chart."))
    profile = str(agent.get("budget_profile") or "analyst")
    checks.append(Check(
        "budget", "ok" if profile != "fast" else "warn", "Room to analyse after the first query",
        f"Budget profile: {profile}.",
        "Use the 'analyst' profile so the agent can break results down and compute growth."
        if profile == "fast" else "",
    ))
    passed = sum(check.status == "ok" for check in checks)
    return {
        "ready": not any(check.status == "fail" for check in checks),
        "score": round(passed / len(checks), 3) if checks else 0.0,
        "checks": [asdict(check) for check in checks],
    }
