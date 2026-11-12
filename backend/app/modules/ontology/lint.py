"""Meaning checks: deterministic rules that catch a definition that lies.

A steward approves words; the formula is what Studio runs. These rules compare
the two using what the compiler already proved, and never call a model:

- ``definition_formula``: a definition names another metric whose inputs the
  formula only partly uses. "Gross margin = net revenue minus cost" over
  ``SUM(net_amount)`` leaves out the returned value net revenue subtracts.
- ``adjustment_ignored``: a metric sums a column that another metric of the same
  view corrects with a returns or cancellation column, and neither its formula
  nor its wording says whether those are included.
- ``ambiguous_name``: one name belongs to approved concepts in two domains, so
  Studio has to ask which one is meant.
- ``unbound_metric``: an approved metric no governed formula computes.

Findings are advice for stewards and release preparation; they never block or
change meaning by themselves. Only concepts and views the caller can see are
checked, so a finding never reveals hidden meaning.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Literal

from app.modules.ontology import access
from app.modules.ontology.contracts import Concept, ConceptKind
from app.modules.ontology.derived import visible_layer
from app.modules.ontology.resolver import names_of, normalise
from app.modules.ontology.stewardship import load_state

Severity = Literal["error", "warning"]

#: A column that takes something back out of a measure.
ADJUSTMENT = re.compile(r"return|retur|refund|cancel|batal|void", re.IGNORECASE)
_QUALIFIED = re.compile(r"`([^`]+)`\.`([^`]+)`")
MAX_FINDINGS = 500
BINDABLE = {ConceptKind.METRIC, ConceptKind.MEASURE}


@dataclass
class Finding:
    rule: str
    severity: Severity
    concept_id: str
    concept: str
    message: str
    evidence: list[str] = field(default_factory=list)


def aggregate_columns(expression: str, dataset: str, model) -> list[set[tuple[str, str]]]:
    """The physical-name-free columns each outermost aggregate reads."""
    from app.modules.agents.semantic.expressions import (
        parse_expression,
        qualify_expression,
        walk,
    )
    from app.sql_dialect.grammar import StarRocksParser

    try:
        qualified = qualify_expression(expression, dataset, model)
        tree = parse_expression(qualified)
    except Exception:  # noqa: BLE001 - an expression we cannot read proves nothing
        return []
    groups = []
    for node in walk(tree):
        if not isinstance(node, StarRocksParser.AggregationFunctionContext):
            continue
        parent, nested = node.parentCtx, False
        while parent is not None:
            if isinstance(parent, StarRocksParser.AggregationFunctionContext):
                nested = True
                break
            parent = parent.parentCtx
        if not nested:
            groups.append({
                (left.casefold(), right.casefold())
                for left, right in _QUALIFIED.findall(node.getText())
            })
    return groups


def _mentions(text: str, index: dict[str, set[str]]) -> set[str]:
    """Concept ids whose names occur in ``text`` as whole words, longest first."""
    question = f" {normalise(text)} "
    taken: list[tuple[int, int]] = []
    found: set[str] = set()
    for name in sorted(index, key=lambda item: (-len(item), item)):
        needle = f" {name} "
        start = question.find(needle)
        while start != -1:
            end = start + len(needle)
            if not any(start < stop and begin < end for begin, stop in taken):
                taken.append((start, end))
                found.update(index[name])
            start = question.find(needle, end - 1)
    return found


async def lint(user: dict, catalog, repository, *, layer: dict | None = None) -> list[Finding]:
    """Meaning checks; ``layer`` reuses a caller's already computed visible layer."""
    state = await load_state(repository)
    domains = state.domains
    visible = {
        domain_id for domain_id, domain in domains.items()
        if domain.status == "ACTIVE" and access.can_see(domain, domains, user)
    }
    concepts: dict[str, Concept] = {
        cid: concept for cid, concept in state.concepts.items()
        if concept.status == "APPROVED" and concept.domain_id in visible
    }

    def qualified(concept: Concept) -> str:
        return access.qualified_path(domains[concept.domain_id], domains) + "." + concept.slug

    if layer is None:
        layer = await visible_layer(user, catalog, repository)
    items = {item["key"]: item for item in layer["items"]}
    visible_views = {
        e["semantic"]["view_id"] for item in layer["items"] for e in item["evidence"]
    }
    # Each bound metric's derived key, and back.
    key_of: dict[str, str] = {}
    concept_of: dict[str, str] = {}
    bound: set[str] = set()
    for binding in sorted(state.representations.values(), key=lambda item: item.id):
        if binding.status != "VALIDATED" or binding.concept_id not in concepts:
            continue
        bound.add(binding.concept_id)
        if binding.semantic.view_id not in visible_views:
            continue
        key = f"metric:{binding.semantic.view_id}.{binding.metric}"
        key_of.setdefault(binding.concept_id, key)
        concept_of.setdefault(key, binding.concept_id)
    inputs: dict[str, set[str]] = {}
    for item in items.values():
        if item["item_type"] == "relation" and item["relation_type"] == "DERIVES":
            inputs.setdefault(item["target_key"], set()).add(item["source_key"])

    def label(key: str) -> str:
        concept_id = concept_of.get(key)
        if concept_id:
            return concepts[concept_id].label
        return items.get(key, {}).get("label") or key

    findings: list[Finding] = []

    # definition_formula
    metric_names: dict[str, set[str]] = {}
    for cid, concept in concepts.items():
        if concept.kind in BINDABLE and cid in key_of:
            for name in names_of(concept, qualified(concept)):
                metric_names.setdefault(name, set()).add(cid)
    for cid in sorted(key_of):
        concept = concepts[cid]
        own = key_of[cid]
        mine = inputs.get(own, set())
        if not mine:
            continue
        for other in sorted(_mentions(concept.definition, metric_names) - {cid}):
            other_key = key_of.get(other)
            theirs = inputs.get(other_key or "", set())
            if not other_key or other_key in mine or not theirs or theirs <= mine:
                continue
            missing = theirs - mine
            if not theirs & mine or not missing:
                continue
            findings.append(Finding(
                rule="definition_formula", severity="error", concept_id=cid,
                concept=qualified(concept),
                message=(
                    f"The definition names {concepts[other].label}, but the formula leaves out "
                    f"{', '.join(sorted(label(key) for key in missing))}: it is built from "
                    f"{', '.join(sorted(label(key) for key in mine))}."
                ),
                evidence=[f"definition: {concept.definition[:200]}",
                          f"{concepts[other].label} is built from "
                          + ", ".join(sorted(label(key) for key in theirs))],
            ))

    # adjustment_ignored
    by_view: dict[str, list[str]] = {}
    for cid, key in key_of.items():
        by_view.setdefault(key.split(":", 1)[1].rpartition(".")[0], []).append(cid)
    from app.modules.agents.semantic.ir import SemanticModelIR

    for view_id, cids in sorted(by_view.items()):
        view = await catalog.published(view_id)
        if view is None:
            continue
        try:
            model = SemanticModelIR.from_ossie(view.definition)
        except Exception:  # noqa: BLE001 - a view we cannot read is checked elsewhere
            continue
        reads: dict[str, list[set[tuple[str, str]]]] = {
            metric.name: aggregate_columns(metric.expression, metric.base_dataset, model)
            for metric in model.metrics
        }
        corrected: dict[tuple[str, str], tuple[str, tuple[str, str]]] = {}
        for name, groups in reads.items():
            for group in groups:
                fixes = sorted(column for column in group if ADJUSTMENT.search(column[1]))
                for column in sorted(group):
                    if fixes and not ADJUSTMENT.search(column[1]):
                        corrected.setdefault(column, (name, fixes[0]))
        for cid in sorted(cids):
            concept = concepts[cid]
            metric_name = key_of[cid].rpartition(".")[2]
            metric = model.metric(metric_name)
            wording = " ".join([
                concept.label, concept.definition, *concept.aliases,
                metric_name, (metric.description or "") if metric else "",
            ])
            if metric is None or ADJUSTMENT.search(wording):
                continue
            for group in reads.get(metric_name, []):
                if any(ADJUSTMENT.search(column[1]) for column in group):
                    continue
                hits = sorted(column for column in group if column in corrected)
                if not hits:
                    continue
                column = hits[0]
                by, fix = corrected[column]
                findings.append(Finding(
                    rule="adjustment_ignored", severity="warning", concept_id=cid,
                    concept=qualified(concept),
                    message=(
                        f"It sums {column[0]}.{column[1]} without {fix[1]}, which {by} uses to "
                        "take returns or cancellations out. Say in the definition whether "
                        "they are included."
                    ),
                    evidence=[f"{view.name} v{view.version} metric {metric_name}: "
                              f"{metric.expression[:200]}"],
                ))
                break

    # ambiguous_name
    owners: dict[str, dict[str, set[str]]] = {}
    for cid, concept in concepts.items():
        for name in names_of(concept, qualified(concept)) - {normalise(qualified(concept))}:
            owners.setdefault(name, {}).setdefault(concept.domain_id, set()).add(cid)
    reported: set[frozenset[str]] = set()
    for name, per_domain in sorted(owners.items()):
        if len(per_domain) < 2:
            continue
        ids = frozenset(cid for group in per_domain.values() for cid in group)
        if ids in reported:
            continue
        reported.add(ids)
        names = sorted(qualified(concepts[cid]) for cid in ids)
        first = min(ids, key=lambda cid: qualified(concepts[cid]))
        findings.append(Finding(
            rule="ambiguous_name", severity="warning", concept_id=first,
            concept=qualified(concepts[first]),
            message=(
                f"'{name}' names {' and '.join(names)}; Studio asks which one is meant "
                "unless the question names a domain."
            ),
        ))

    # unbound_metric
    for cid, concept in sorted(concepts.items()):
        if concept.kind == ConceptKind.METRIC and cid not in bound:
            findings.append(Finding(
                rule="unbound_metric", severity="warning", concept_id=cid,
                concept=qualified(concept),
                message="No validated binding computes it, so Studio cannot answer with it.",
            ))

    order = {"error": 0, "warning": 1}
    findings.sort(key=lambda item: (order[item.severity], item.concept, item.rule))
    return findings[:MAX_FINDINGS]


async def lint_report(user: dict, catalog, repository) -> dict:
    findings = await lint(user, catalog, repository)
    counts = {"error": 0, "warning": 0}
    for item in findings:
        counts[item.severity] += 1
    return {"findings": [asdict(item) for item in findings], "counts": counts}
