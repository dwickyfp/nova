"""Deterministic diagnosis of frozen release evidence; explanations remain hypotheses."""

from __future__ import annotations

import math
from dataclasses import asdict
from typing import Literal

from fastapi import HTTPException
from pydantic import Field

from app.modules.agents.quality_scoring import MEASURED_COUNTS, PromotionGates
from app.modules.agents.releases import get_manifest, safe_content
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.versions import configuration, revision_id
from app.modules.intelligence.contracts import Contract, SemanticRef, fingerprint

RESTORABLE_FIELDS = frozenset(
    {
        "instructions_response",
        "instructions_orchestration",
        "response_style",
        "default_tools",
        "default_skills",
        "discoverable_skills",
        "model_provider_id",
        "model_name",
        "policy",
        "budget_seconds",
        "budget_tokens",
        "budget_profile",
        "tool_not_accessible",
    }
)
REMEDIATION_INSTRUCTIONS = {
    "WRONG_SEMANTIC_METRIC": "Select metric names from the authorized published Semantic View. "
    "Request clarification when metric aliases conflict or the business target is ambiguous.",
    "WRONG_TOOL": "Select registered tools that support the task and its governed evidence.",
    "BAD_TOOL_ARGUMENTS": "Use registered tool schemas. Request required inputs before dispatch.",
    "UNSUPPORTED_CONCLUSION": "Support numeric conclusions with verified evidence and preserve "
    "metric, currency, and period identity. State when supporting evidence is unavailable.",
    "MISSING_CONTEXT": "Request missing business inputs when governed evidence cannot be resolved.",
    "INCOMPLETE_ANSWER": "Complete the requested task with verified results, evidence references, "
    "and an explicit account of unresolved inputs.",
    "POLICY_FAILURE": "Check authorization, policy, and consent before each governed effect.",
    "ACTION_VERIFICATION_FAILED": "Report an action as complete only after authorized readback "
    "verifies its configuration. Reconcile uncertain mutations before further dispatch.",
    "TOO_SLOW": "Use bounded reads and reuse compatible verified evidence for the objective.",
    "INEFFICIENT_EXECUTION": "Avoid repeating successful tool reads when their governed evidence "
    "still answers the requested objective.",
}


class RemediationPatch(Contract):
    id: str = Field(min_length=1, max_length=64)
    kind: Literal["restore_configuration", "append_instruction", "semantic_changes"]
    description: str = Field(max_length=512)
    hypothesis: bool = True
    base_revision: str = Field(min_length=1, max_length=64)
    source_version_id: str | None = Field(default=None, max_length=64)
    source_configuration_fingerprint: str | None = Field(default=None, max_length=64)
    fields: list[str] = Field(default_factory=list, max_length=16)
    instruction: str | None = Field(default=None, max_length=2000)
    semantic: SemanticRef | None = None
    changes: list[dict] = Field(default_factory=list, max_length=30)


def score_index(run: dict) -> dict[tuple, dict]:
    return {
        (
            result["case_id"],
            result["case_revision"],
            score["scorer"],
            score.get("scorer_version"),
        ): {
            **score,
            "trace_id": result.get("trace_id"),
            "duration_ms": result.get("duration_ms"),
            "counts": (result.get("trace") or {}).get("counts") or {},
        }
        for result in run.get("results", [])
        for score in [*result.get("scores", []), *result.get("budget_scores", [])]
    }


def evaluation_identity(run: dict) -> str | None:
    """Legacy runs may lack a digest, but missing frozen evidence is never compatible."""
    cases = run.get("cases")
    if (
        not isinstance(cases, list)
        or not cases
        or len(cases) > 100
        or not run.get("scorer_set_version")
        or run.get("source") == "production"
        or run.get("status") == "running"
        or (run.get("case_fingerprint") and run["case_fingerprint"] != fingerprint(cases))
    ):
        return None
    try:
        gates = PromotionGates.model_validate(run.get("gates") or {})
        frozen = {(case["id"], case["revision"]) for case in cases}
        results = run.get("results", [])
        if len(frozen) != len(cases) or len(results) != len(cases):
            return None
        if {(item["case_id"], item["case_revision"]) for item in results} != frozen:
            return None
        expected = {
            (case["id"], case["revision"], assertion["scorer"])
            for case in cases
            for assertion in [
                *case["assertions"],
                *[a.model_dump() for a in gates.performance_assertions()],
            ]
        }
        scores = score_index(run)
        recorded_count = sum(
            len(item.get("scores", [])) + len(item.get("budget_scores", [])) for item in results
        )
        if {(key[0], key[1], key[2]) for key in scores} != expected:
            return None
        if (
            any(not key[3] for key in scores)
            or len(scores) != len(expected)
            or recorded_count != len(scores)
        ):
            return None
        if any(score["status"] not in {"pass", "fail", "unavailable"} for score in scores.values()):
            return None
        return fingerprint(
            {
                "cases": sorted(cases, key=lambda item: item["id"]),
                "gates": gates.model_dump(mode="json"),
                "scorer_set_version": run["scorer_set_version"],
                "scorers": sorted(scores),
            }
        )
    except (KeyError, TypeError, ValueError):
        return None


def compatible(left: dict, right: dict) -> bool:
    identity = evaluation_identity(left)
    return identity is not None and identity == evaluation_identity(right)


def _number(value) -> float | None:
    return (
        float(value)
        if type(value) in {int, float} and math.isfinite(value) and value >= 0
        else None
    )


def release_ref(run: dict | None) -> dict | None:
    if run is None:
        return None
    return {
        key: run.get(key)
        for key in ("id", "version_id", "manifest_id", "manifest_fingerprint", "created_at")
    }


def regressions(before: dict, after: dict) -> list[dict]:
    if not compatible(before, after):
        return []
    left, right = score_index(before), score_index(after)
    findings = []
    for key in sorted(left):
        if left[key]["status"] != "pass" or right[key]["status"] == "pass":
            continue
        measurements = []
        for name in ["duration_ms", *sorted(MEASURED_COUNTS)]:
            a = left[key].get(name) if name == "duration_ms" else left[key]["counts"].get(name)
            b = right[key].get(name) if name == "duration_ms" else right[key]["counts"].get(name)
            a, b = _number(a), _number(b)
            if a is not None and b is not None:
                measurements.append({"name": name, "before": a, "after": b, "delta": b - a})
        findings.append(
            {
                "case_id": key[0],
                "case_revision": key[1],
                "scorer": key[2],
                "scorer_version": key[3],
                "before": "pass",
                "after": right[key]["status"],
                "before_trace_id": left[key]["trace_id"],
                "after_trace_id": right[key]["trace_id"],
                "failure_taxonomy": right[key].get("failure_taxonomy"),
                "measurements": measurements,
                "hypothesis": False,
            }
        )
    return findings[:100]


def diagnose_history(
    runs: list[dict], *, current_version_id: str | None = None, current_run_id: str | None = None
) -> dict:
    ordered = sorted(runs[:100], key=lambda run: (run.get("created_at", ""), run["id"]))
    evaluated = [
        run
        for run in ordered
        if run.get("source") != "production" and run.get("status") != "running"
    ]
    current = next(
        (
            run
            for run in reversed(evaluated)
            if (not current_version_id or run.get("version_id") == current_version_id)
            and (not current_run_id or run["id"] == current_run_id)
        ),
        None,
    )
    baseline = (
        next(
            (
                run
                for run in reversed(evaluated[: evaluated.index(current)])
                if current
                and compatible(run, current)
                and run.get("status") == "passed"
                and run.get("promotion_eligible") is True
            ),
            None,
        )
        if current
        else None
    )
    first_bad = (
        next(
            (
                run
                for run in evaluated[evaluated.index(baseline) + 1 : evaluated.index(current) + 1]
                if compatible(baseline, run) and regressions(baseline, run)
            ),
            None,
        )
        if baseline
        else None
    )
    current_regressions = regressions(baseline, current) if baseline and current else []
    requirements = []
    if current is None:
        requirements.append(
            {
                "code": "CURRENT_RELEASE_UNEVALUATED",
                "detail": "Evaluate the current release against a frozen dataset.",
            }
        )
    elif evaluation_identity(current) is None:
        requirements.append(
            {
                "code": "EVALUATION_INCOMPLETE",
                "detail": "A complete frozen dataset and versioned scores are required.",
            }
        )
    elif baseline is None:
        requirements.append(
            {
                "code": "COMPATIBLE_BASELINE_REQUIRED",
                "detail": "No earlier passing evaluation has compatible cases, scorers, and gates.",
            }
        )
    return {
        "known_good": release_ref(baseline),
        "first_bad": release_ref(first_bad),
        "current": release_ref(current),
        "regressions": current_regressions,
        "first_bad_regressions": regressions(baseline, first_bad) if baseline and first_bad else [],
        "requirements": requirements,
    }


def dependency_changes(before: dict, after: dict) -> list[dict]:
    changes = []
    left, right = before.get("dependencies") or {}, after.get("dependencies") or {}
    for category in sorted(set(left) | set(right)):
        if fingerprint(left.get(category)) == fingerprint(right.get(category)):
            continue
        item = {
            "category": category,
            "before_fingerprint": fingerprint(left.get(category)),
            "after_fingerprint": fingerprint(right.get(category)),
            "hypothesis": True,
        }
        if category == "configuration":
            item["fields"] = sorted(
                key
                for key in set(left[category]) | set(right[category])
                if fingerprint(left[category].get(key)) != fingerprint(right[category].get(key))
            )[:32]
        changes.append(item)
    return changes[:32]


def regression_candidates(run: dict) -> list[dict]:
    failed = {item["case_id"] for item in run.get("results", []) if item.get("status") != "passed"}
    return [
        {
            "id": fingerprint([run["id"], case["id"], case["revision"], "regression-case-v1"]),
            "run_id": run["id"],
            "case_id": case["id"],
            "case_revision": case["revision"],
            "case_fingerprint": fingerprint(case),
            "scorers": [a["scorer"] for a in case["assertions"]],
            "review_required": True,
        }
        for case in run.get("cases", [])
        if case["id"] in failed
    ][:32]


def instruction_patch(agent: dict, failures: list[str]) -> RemediationPatch | None:
    text = "\n".join(
        REMEDIATION_INSTRUCTIONS[key]
        for key in sorted(set(failures))
        if key in REMEDIATION_INSTRUCTIONS
    )
    if not text:
        return None
    return RemediationPatch(
        id=fingerprint([revision_id(agent), text]),
        kind="append_instruction",
        description="Add evidence and task requirements for the observed failures.",
        base_revision=revision_id(agent),
        fields=["instructions_response"],
        instruction=text,
    )


async def report(
    agent: dict, user: dict, runs: list[dict], *, current_run: dict | None = None
) -> dict:
    from app.modules.intelligence.semantic_views import semantic_view_service

    active = next(
        (
            run
            for run in sorted(runs, key=lambda item: item.get("created_at", ""), reverse=True)
            if agent.get("release_manifest_id")
            and run.get("manifest_id") == agent["release_manifest_id"]
        ),
        None,
    )
    current_version = (current_run or active or {}).get("version_id") or agent.get(
        "config_revision"
    )
    history = diagnose_history(
        runs,
        current_version_id=current_version,
        current_run_id=current_run.get("id") if current_run else None,
    )
    manifests = {}
    for ref in [history["known_good"], history["first_bad"], history["current"]]:
        if not ref or ref["manifest_id"] in manifests:
            continue
        manifest = await get_manifest(
            agent["agent_id"], user["username"], manifest_id=ref["manifest_id"]
        )
        if (
            manifest
            and manifest.get("fingerprint") == fingerprint(manifest.get("dependencies"))
            and manifest.get("fingerprint") == ref["manifest_fingerprint"]
        ):
            manifests[ref["manifest_id"]] = manifest
        else:
            history["requirements"].append(
                {
                    "code": "MANIFEST_UNAVAILABLE",
                    "detail": "An evaluated manifest is missing or has a different fingerprint.",
                }
            )
    baseline = manifests.get((history["known_good"] or {}).get("manifest_id"))
    current = manifests.get((history["current"] or {}).get("manifest_id"))
    first_bad = manifests.get((history["first_bad"] or {}).get("manifest_id"))
    changes = dependency_changes(baseline, current) if baseline and current else []
    findings, patches = [], []
    if baseline and current and history["regressions"]:
        source = baseline["dependencies"]["configuration"]
        target = configuration(agent)
        fields = sorted(key for key in RESTORABLE_FIELDS if source.get(key) != target.get(key))
        if fields:
            patches.append(
                RemediationPatch(
                    id=fingerprint([baseline["version_id"], fields, revision_id(agent)]),
                    kind="restore_configuration",
                    description="Restore configuration from the compatible known-good release.",
                    base_revision=revision_id(agent),
                    source_version_id=baseline["version_id"],
                    source_configuration_fingerprint=fingerprint(source),
                    fields=fields,
                )
            )
    authorized = {}
    for manifest in [baseline, current]:
        for ref in (manifest or {}).get("dependencies", {}).get("semantic_views", [])[:20]:
            identity = (ref["view_id"], ref["version"], ref["fingerprint"])
            if identity in authorized:
                continue
            try:
                authorized[identity] = await semantic_view_service.get_version_for_agent(
                    *identity, user, agent_id=agent["agent_id"]
                )
            except HTTPException:
                history["requirements"].append(
                    {
                        "code": "SEMANTIC_DEPENDENCY_UNAVAILABLE",
                        "detail": "A pinned semantic dependency is unavailable to this caller.",
                    }
                )
    for identity, view in authorized.items():
        ir = SemanticModelIR.from_ossie(view["definition"])
        aliases: dict[str, set[str]] = {}
        for metric in ir.metrics:
            for alias in [metric.name, *metric.synonyms]:
                aliases.setdefault(" ".join(alias.casefold().split()), set()).add(metric.name)
        collisions = [
            {"alias": alias, "metrics": sorted(names)}
            for alias, names in sorted(aliases.items())
            if len(names) > 1
        ][:32]
        if collisions:
            findings.append(
                {
                    "category": "SEMANTIC_ALIAS_COLLISION",
                    "semantic": {
                        "view_id": identity[0],
                        "version": identity[1],
                        "fingerprint": identity[2],
                    },
                    "collisions": collisions,
                    "hypothesis": True,
                    "detail": "Ambiguous aliases may contribute to metric selection regressions.",
                }
            )
        previous = next(
            (
                candidate
                for key, candidate in authorized.items()
                if key[0] == identity[0] and key[1] < identity[1]
            ),
            None,
        )
        if previous:
            old = {
                metric.name: asdict(metric)
                for metric in SemanticModelIR.from_ossie(previous["definition"]).metrics
            }
            new = {metric.name: asdict(metric) for metric in ir.metrics}
            drift = [
                {
                    "metric": name,
                    "before_fingerprint": fingerprint(old.get(name)),
                    "after_fingerprint": fingerprint(new.get(name)),
                    "fields": sorted(
                        key
                        for key in set(old.get(name) or {}) | set(new.get(name) or {})
                        if (old.get(name) or {}).get(key) != (new.get(name) or {}).get(key)
                    ),
                }
                for name in sorted(set(old) | set(new))
                if old.get(name) != new.get(name)
            ][:32]
            if drift:
                findings.append(
                    {
                        "category": "SEMANTIC_DEFINITION_CHANGE",
                        "semantic": {
                            "view_id": identity[0],
                            "version": identity[1],
                            "fingerprint": identity[2],
                        },
                        "changes": drift,
                        "hypothesis": True,
                        "detail": "Semantic definition changes may contribute to the regression.",
                    }
                )
        if (
            collisions
            and view.get("owner_name") == user["username"]
            and current
            and identity
            in [
                (ref["view_id"], ref["version"], ref["fingerprint"])
                for ref in current["dependencies"].get("semantic_views", [])
            ]
        ):
            ambiguous = {item["alias"] for item in collisions}
            semantic_changes = [
                {
                    "kind": "synonyms",
                    "name": metric.name,
                    "synonyms": [
                        alias
                        for alias in metric.synonyms
                        if " ".join(alias.casefold().split()) not in ambiguous
                    ],
                }
                for metric in ir.metrics
                if any(" ".join(alias.casefold().split()) in ambiguous for alias in metric.synonyms)
            ][:30]
            if semantic_changes:
                patches.append(
                    RemediationPatch(
                        id=fingerprint([identity, semantic_changes]),
                        kind="semantic_changes",
                        description="Remove ambiguous synonyms and preserve metric definitions.",
                        base_revision=revision_id(agent),
                        semantic=SemanticRef(
                            view_id=identity[0], version=identity[1], fingerprint=identity[2]
                        ),
                        changes=semantic_changes,
                    )
                )
    selected = current_run or next(
        (run for run in runs if run["id"] == (history["current"] or {}).get("id")), None
    )
    if selected:
        fallback = instruction_patch(
            agent,
            [
                score.get("failure_taxonomy")
                for result in selected.get("results", [])
                for score in [*result.get("scores", []), *result.get("budget_scores", [])]
                if score.get("failure_taxonomy")
            ],
        )
        if fallback:
            patches.append(fallback)
    return safe_content(
        {
            "schema_version": 2,
            "agent_id": agent["agent_id"],
            **history,
            "changed_dependencies": changes,
            "first_bad_changed_dependencies": dependency_changes(baseline, first_bad)
            if baseline and first_bad
            else [],
            "findings": findings[:100],
            "patches": [patch.model_dump(mode="json", exclude_none=True) for patch in patches[:16]],
            "regression_candidates": regression_candidates(selected) if selected else [],
            "review_required": True,
        }
    )
