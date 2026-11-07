"""The derived layer: business meaning Nova compiles from published Semantic Views.

Stewards write official meaning; the compiler writes everything a published
Semantic View already proves, so the graph is never empty:

- a dataset with a grain is a business entity (one per physical table, merged
  across views);
- a categorical or time field is a dimension related to its entity;
- a public metric is a metric that measures its base entity;
- a many-to-one relationship links two entities, named by the role its key
  plays (``salesman_id`` makes the salesman role of Employee);
- a metric built from other metrics is derived from them (``DERIVES`` points
  from each input to the result, as in the official rules), proven on the
  parsed expression, never guessed from names;
- metrics with the same claim fingerprint in different views are equivalent.

Every item records the evidence it came from and a rule-based confidence. The
layer is deterministic: the same published definitions give the same items.
Derived items are context and candidates only. They are never released, never
bound, and never override an approved concept.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import Field

from app.modules.agents.semantic.ir import FieldKind, SemanticModelIR
from app.modules.intelligence.contracts import Contract, SemanticRef, fingerprint
from app.modules.ontology.contracts import Actor, ConceptKind, OntologyRecord, RelationType

logger = logging.getLogger(__name__)

COMPILER_VERSION = "nova-derived:2"
#: Field types that hold amounts and counts; they feed metrics, not breakdowns.
NUMERIC = frozenset(
    {"integer", "int", "bigint", "smallint", "tinyint", "decimal", "double", "float",
     "number", "numeric", "real", "largeint"}
)
EvidenceMethod = Literal["dataset", "field", "relationship", "metric", "metric_expression", "claim"]
#: Rule-based confidence of one piece of evidence, before corroboration.
BASE_CONFIDENCE: dict[str, float] = {
    "dataset": 0.9,
    "dataset_without_grain": 0.7,
    "field": 0.9,
    "relationship": 0.95,
    "relationship_unknown": 0.6,
    "metric": 1.0,
    "metric_expression": 0.95,
    "claim": 0.9,
}
#: Added per further view that proves the same item.
CORROBORATION = 0.05


class DerivedEvidence(Contract):
    """One published object that proves a derived item."""

    semantic: SemanticRef
    view_name: str = Field(min_length=1, max_length=256)
    method: EvidenceMethod
    source: str = Field(min_length=1, max_length=300)
    confidence: float = Field(ge=0, le=1)
    #: What this view says the item is called and means; the item reads them all.
    label: str = Field(default="", max_length=200)
    definition: str = Field(default="", max_length=2000)
    aliases: list[str] = Field(default_factory=list, max_length=32)


class DerivedItem(OntologyRecord):
    item_type: Literal["concept", "relation"]
    key: str = Field(min_length=1, max_length=400)
    concept_kind: ConceptKind | None = None
    label: str = Field(default="", max_length=200)
    definition: str = Field(default="", max_length=2000)
    aliases: list[str] = Field(default_factory=list, max_length=32)
    unit: str | None = Field(default=None, max_length=64)
    is_time: bool = False
    #: The metric whose claim this concept is (``view_id`` and name), for metrics.
    metric: str | None = Field(default=None, max_length=300)
    claim_fingerprint: str | None = Field(default=None, max_length=128)
    relation_type: RelationType | None = None
    source_key: str | None = Field(default=None, max_length=400)
    target_key: str | None = Field(default=None, max_length=400)
    role: str | None = Field(default=None, max_length=128)
    evidence: list[DerivedEvidence] = Field(default_factory=list, max_length=64)
    confidence: float = Field(default=0, ge=0, le=1)
    status: Literal["ACTIVE", "RETIRED"] = "ACTIVE"
    compiler_version: str = COMPILER_VERSION


class Compilation(OntologyRecord):
    """One compiler run over one published view version."""

    view_id: str = Field(min_length=1, max_length=128)
    view_name: str = Field(min_length=1, max_length=256)
    version: int = Field(ge=1)
    view_fingerprint: str = Field(min_length=1, max_length=128)
    counts: dict[str, int] = Field(default_factory=dict)
    compiler_version: str = COMPILER_VERSION


def item_id(key: str) -> str:
    return "drv_" + fingerprint(key)[:40]


@dataclass
class Draft:
    """A derived item compiled from one view, before it is merged with others."""

    item_type: Literal["concept", "relation"]
    key: str
    evidence: DerivedEvidence
    concept_kind: ConceptKind | None = None
    label: str = ""
    definition: str = ""
    aliases: list[str] = field(default_factory=list)
    unit: str | None = None
    is_time: bool = False
    metric: str | None = None
    claim_fingerprint: str | None = None
    relation_type: RelationType | None = None
    source_key: str | None = None
    target_key: str | None = None
    role: str | None = None


# --- naming ------------------------------------------------------------------

def humanize(name: str) -> str:
    words = [word for word in re.split(r"[_\W]+", name) if word]
    return " ".join(words).strip().capitalize()


def singular(name: str) -> str:
    """English plural of the last word to singular; table names are mostly English."""
    words = name.split(" ")
    last = words[-1]
    lower = last.lower()
    if lower.endswith("ies") and len(lower) > 4:
        last = last[:-3] + "y"
    elif lower.endswith(("sses", "xes", "ches", "shes", "statuses", "buses")):
        last = last[:-2]  # addresses, boxes, batches; not warehouses or purchases
    elif lower.endswith("s") and not lower.endswith(("ss", "us", "is")) and len(lower) > 3:
        last = last[:-1]
    return " ".join([*words[:-1], last])


def _column(expression: str) -> str:
    """The column a simple field expression reads, else the expression itself."""
    text = expression.strip().strip("`")
    tail = text.split(".")[-1].strip("`")
    return tail if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tail) else text


def role_name(column: str) -> str:
    """``salesman_id`` → ``salesman``; the key column names the role it plays."""
    stem = re.sub(r"(?:_|^)(?:id|key|code|no|number)$", "", column, flags=re.IGNORECASE)
    return humanize(stem or column).lower()


def entity_key(source: str) -> str:
    return "entity:" + source.strip().strip("`").casefold()


def dimension_key(source: str, column: str) -> str:
    return f"dimension:{source.strip().strip('`').casefold()}.{column.casefold()}"


def metric_key(view_id: str, name: str) -> str:
    return f"metric:{view_id}.{name}"


def relation_key(kind: RelationType, source: str, target: str, role: str | None = None) -> str:
    return f"relation:{kind.value}:{source}->{target}:{role or ''}"


def _aliases(*values) -> list[str]:
    seen: dict[str, str] = {}
    for value in values:
        for item in value if isinstance(value, list | tuple) else [value]:
            text = " ".join(str(item or "").split())
            if text and len(text) <= 200:
                seen.setdefault(text.casefold(), text)
    return sorted(seen.values(), key=str.casefold)[:32]


# --- metric expressions --------------------------------------------------------

def _canonical(text: str) -> str:
    return text.replace("`", "").replace(" ", "").casefold()


def metric_components(expression: str) -> tuple[str | None, list[str]]:
    """The metric's own canonical aggregate, and the aggregates it is built from.

    A single aggregate (``SUM(x)``) is atomic. ``SUM(a) / SUM(b)`` is built from
    ``SUM(a)`` and ``SUM(b)``. ``SUM(a - b)`` is built from ``SUM(a)`` and
    ``SUM(b)``, because a sum distributes over addition and subtraction; no other
    aggregate is decomposed that way.
    """
    from app.modules.agents.semantic.expressions import parse_expression, walk
    from app.sql_dialect.grammar import StarRocksParser

    try:
        tree = parse_expression(expression)
    except Exception:  # noqa: BLE001 - an unparsable expression simply proves nothing
        return None, []
    aggregates = []
    for node in walk(tree):
        if not isinstance(node, StarRocksParser.AggregationFunctionContext):
            continue
        parent = node.parentCtx
        nested = False
        while parent is not None:
            if isinstance(parent, StarRocksParser.AggregationFunctionContext):
                nested = True
                break
            parent = parent.parentCtx
        if not nested:
            aggregates.append(node)
    if not aggregates:
        return None, []
    if len(aggregates) > 1:
        return None, [_canonical(node.getText()) for node in aggregates]
    only = aggregates[0]
    whole = _canonical(tree.getText()) == _canonical(only.getText())
    own = _canonical(only.getText()) if whole else None
    name = only.getText().split("(", 1)[0].strip().upper()
    if name != "SUM" or "DISTINCT" in only.getText().upper()[:16]:
        return own, []
    argument = next(
        (child for child in only.children or []
         if isinstance(child, StarRocksParser.ExpressionContext)),
        None,
    )
    terms = _additive_terms(argument) if argument is not None else []
    if len(terms) < 2:
        return own, []
    return own, [f"sum({_canonical(term.getText())})" for term in terms]


def _additive_terms(expression) -> list:
    """The top-level ``+``/``-`` operands of an expression, left to right."""
    from app.sql_dialect.grammar import StarRocksParser

    node = expression
    # Unwrap the single-child wrappers the grammar puts around a value expression.
    while (
        not isinstance(node, StarRocksParser.ArithmeticBinaryContext)
        and len(getattr(node, "children", None) or []) == 1
    ):
        node = node.children[0]
    if not isinstance(node, StarRocksParser.ArithmeticBinaryContext):
        return []
    operator = getattr(getattr(node, "operator", None), "text", "")
    if operator not in {"+", "-"}:
        return []
    left, right = node.left, node.right
    return [*(_additive_terms(left) or [left]), right]


# --- compile one view ------------------------------------------------------------

def derive(view) -> list[Draft]:
    """Every derived item one published view proves. Pure and deterministic."""
    from app.modules.agents.semantic.suggestions import identifier_fields
    from app.modules.ontology.representations import public_metrics

    model = SemanticModelIR.from_ossie(view.definition)
    ref = SemanticRef(view_id=view.view_id, version=view.version, fingerprint=view.fingerprint)

    def evidence(
        method: EvidenceMethod, source: str, rule: str | None = None
    ) -> DerivedEvidence:
        return DerivedEvidence(
            semantic=ref, view_name=view.name[:256], method=method, source=source[:300],
            confidence=BASE_CONFIDENCE[rule or method],
        )

    drafts: list[Draft] = []
    identifiers = identifier_fields(model)
    entities: dict[str, str] = {}
    for dataset in model.datasets:
        key = entity_key(dataset.source or dataset.name)
        entities[dataset.name] = key
        drafts.append(Draft(
            item_type="concept", key=key, concept_kind=ConceptKind.BUSINESS_ENTITY,
            label=singular(humanize(dataset.name)),
            definition=dataset.description,
            aliases=_aliases(dataset.synonyms),
            evidence=evidence(
                "dataset", f"dataset:{dataset.name}",
                None if dataset.grain.keys else "dataset_without_grain",
            ),
        ))
        for item in dataset.fields:
            if (dataset.name, item.name) in identifiers or item.kind != FieldKind.DIMENSION:
                continue
            if not item.is_time and (item.datatype or "").casefold() in NUMERIC:
                continue
            column = _column(item.expression)
            dimension = dimension_key(dataset.source or dataset.name, column)
            drafts.append(Draft(
                item_type="concept", key=dimension, concept_kind=ConceptKind.DIMENSION,
                label=humanize(column), definition=item.description,
                aliases=_aliases(item.synonyms, humanize(item.name) if item.name != column else []),
                is_time=item.is_time,
                evidence=evidence("field", f"field:{dataset.name}.{item.name}"),
            ))
            drafts.append(Draft(
                item_type="relation",
                key=relation_key(RelationType.RELATED_TO, dimension, key),
                relation_type=RelationType.RELATED_TO, source_key=dimension, target_key=key,
                evidence=evidence("field", f"field:{dataset.name}.{item.name}"),
            ))

    for relationship in model.relationships:
        source = entities.get(relationship.from_dataset)
        target = entities.get(relationship.to_dataset)
        if source is None or target is None or source == target:
            continue
        if relationship.cardinality == "one_to_many":
            source, target = target, source
            columns = relationship.to_columns
            owner = model.dataset(relationship.to_dataset)
        else:
            columns = relationship.from_columns
            owner = model.dataset(relationship.from_dataset)
        column = columns[0] if columns else ""
        key_field = owner.field(column) if owner is not None else None
        if key_field is not None:
            column = _column(key_field.expression)
        target_name = model.dataset(
            relationship.to_dataset if relationship.cardinality != "one_to_many"
            else relationship.from_dataset
        )
        role = role_name(column) if column else None
        # A key named after its target (outlet_id to Outlet, plan_id to Route plan) is no role.
        if target_name is not None and role and singular(
            humanize(target_name.name)
        ).lower().endswith(role):
            role = None
        known = relationship.cardinality in {"many_to_one", "one_to_one", "one_to_many"}
        drafts.append(Draft(
            item_type="relation", key=relation_key(RelationType.RELATED_TO, source, target, role),
            relation_type=RelationType.RELATED_TO, source_key=source, target_key=target, role=role,
            evidence=evidence(
                "relationship", f"relationship:{relationship.name}",
                None if known else "relationship_unknown",
            ),
        ))

    claims = public_metrics(view.definition)
    # An aggregate means the same thing only over the same base dataset: COUNT(*)
    # of routes is not COUNT(*) of stops.
    atomic: dict[tuple[str, str], str] = {}
    built: dict[str, list[str]] = {}
    for metric in model.metrics:
        if metric.name not in claims:
            continue
        own, parts = metric_components(metric.expression)
        if own:
            atomic.setdefault((metric.base_dataset, own), metric.name)
        built[metric.name] = parts
    for metric in model.metrics:
        claim = claims.get(metric.name)
        if claim is None:
            continue
        key = metric_key(view.view_id, metric.name)
        drafts.append(Draft(
            item_type="concept", key=key, concept_kind=ConceptKind.METRIC,
            label=humanize(metric.name), definition=metric.description,
            aliases=_aliases(metric.synonyms, metric.name), unit=metric.unit,
            metric=f"{view.view_id}.{metric.name}", claim_fingerprint=claim.fingerprint,
            evidence=evidence("metric", f"metric:{metric.name}"),
        ))
        base = entities.get(metric.base_dataset)
        if base is not None:
            drafts.append(Draft(
                item_type="relation", key=relation_key(RelationType.MEASURES, key, base),
                relation_type=RelationType.MEASURES, source_key=key, target_key=base,
                evidence=evidence("metric", f"metric:{metric.name}"),
            ))
        dependencies = {
            *metric.dependencies,
            *(
                atomic[(metric.base_dataset, part)] for part in built[metric.name]
                if (metric.base_dataset, part) in atomic
            ),
        }
        for dependency in sorted(dependencies - {metric.name}):
            if dependency not in claims:
                continue
            source = metric_key(view.view_id, dependency)
            drafts.append(Draft(
                item_type="relation", key=relation_key(RelationType.DERIVES, source, key),
                relation_type=RelationType.DERIVES, source_key=source, target_key=key,
                evidence=evidence("metric_expression", f"metric:{metric.name}"),
            ))
    return drafts


# --- merge into the shared layer ------------------------------------------------------

def _confidence(evidence: list[DerivedEvidence]) -> float:
    if not evidence:
        return 0.0
    views = {item.semantic.view_id for item in evidence}
    return round(min(1.0, max(item.confidence for item in evidence)
                     + CORROBORATION * (len(views) - 1)), 4)


def _texts(evidence: list[DerivedEvidence]) -> dict[str, Any]:
    """Name, meaning and aliases from every view, independent of compile order."""
    return {
        "label": next((item.label for item in evidence if item.label), ""),
        "definition": next((item.definition for item in evidence if item.definition), ""),
        "aliases": _aliases(*(item.aliases for item in evidence)),
    }


def _merge(old: DerivedItem | None, drafts: list[Draft], view_id: str, actor: Actor) -> DerivedItem:
    kept = [item for item in (old.evidence if old else []) if item.semantic.view_id != view_id]
    fresh = {
        draft.evidence.source: draft.evidence.model_copy(update={
            "label": draft.label[:200], "definition": draft.definition[:2000],
            "aliases": draft.aliases[:32],
        })
        for draft in drafts
    }
    evidence = sorted(
        [*kept, *fresh.values()], key=lambda item: (item.semantic.view_id, item.source)
    )[:64]
    first = drafts[0]
    body: dict[str, Any] = {
        "item_type": first.item_type, "key": first.key, "concept_kind": first.concept_kind,
        **_texts(evidence), "unit": first.unit, "is_time": first.is_time,
        "metric": first.metric, "claim_fingerprint": first.claim_fingerprint,
        "relation_type": first.relation_type, "source_key": first.source_key,
        "target_key": first.target_key, "role": first.role,
        "evidence": evidence, "confidence": _confidence(evidence),
        "status": "ACTIVE", "compiler_version": COMPILER_VERSION,
    }
    if old is not None:
        return old.model_copy(update={**body, "actor": actor})
    return DerivedItem.model_validate({"id": item_id(first.key), "actor": actor, **body})


def _equivalences(items: dict[str, DerivedItem]) -> dict[str, list[Draft]]:
    """Metrics with one claim fingerprint in different views are the same metric."""
    by_claim: dict[str, list[DerivedItem]] = {}
    for item in items.values():
        if (
            item.status == "ACTIVE" and item.concept_kind == ConceptKind.METRIC
            and item.claim_fingerprint
        ):
            by_claim.setdefault(item.claim_fingerprint, []).append(item)
    expected: dict[str, list[Draft]] = {}
    for group in by_claim.values():
        group = sorted(group, key=lambda item: item.key)
        for index, left in enumerate(group):
            for right in group[index + 1:]:
                if left.evidence[0].semantic.view_id == right.evidence[0].semantic.view_id:
                    continue
                key = relation_key(RelationType.EQUIVALENT_TO, left.key, right.key)
                expected[key] = [
                    Draft(
                        item_type="relation", key=key, relation_type=RelationType.EQUIVALENT_TO,
                        source_key=left.key, target_key=right.key,
                        evidence=item.evidence[0].model_copy(update={
                            "method": "claim", "confidence": BASE_CONFIDENCE["claim"],
                        }),
                    )
                    for item in (left, right)
                ]
    return expected


async def compile_view(view, actor: Actor, repository) -> Compilation:
    """Recompile one published view into the shared derived layer, idempotently."""
    drafts = derive(view)
    grouped: dict[str, list[Draft]] = {}
    for draft in drafts:
        grouped.setdefault(draft.key, []).append(draft)
    current = {item.key: item for item in await repository.current("derived", DerivedItem)}
    merged: dict[str, DerivedItem] = dict(current)
    for key, items in grouped.items():
        merged[key] = _merge(current.get(key), items, view.view_id, actor)
    for key, old in current.items():
        if key in grouped or not any(e.semantic.view_id == view.view_id for e in old.evidence):
            continue
        evidence = [e for e in old.evidence if e.semantic.view_id != view.view_id]
        merged[key] = old.model_copy(update={
            "evidence": evidence, "confidence": _confidence(evidence), "actor": actor,
            "status": "ACTIVE" if evidence else "RETIRED",
            **(_texts(evidence) if evidence else {}),
        })
    expected = _equivalences(merged)
    for key, item in list(merged.items()):
        if item.relation_type != RelationType.EQUIVALENT_TO:
            continue
        if key not in expected and item.status == "ACTIVE":
            merged[key] = item.model_copy(update={"status": "RETIRED", "actor": actor})
    for key, items in expected.items():
        evidence = [draft.evidence for draft in items]
        old = merged.get(key)
        merged[key] = _merge(old, items[:1], "", actor).model_copy(update={
            "evidence": evidence, "confidence": _confidence(evidence),
        }) if old is None else old.model_copy(update={
            "evidence": evidence, "confidence": _confidence(evidence),
            "status": "ACTIVE", "actor": actor,
        })
    changed = await repository.save_many("derived", [
        (item, current[key].revision if key in current else 0) for key, item in merged.items()
    ])
    active = [item for item in merged.values() if item.status == "ACTIVE"
              and any(e.semantic.view_id == view.view_id for e in item.evidence)]
    counts = {
        "entities": sum(item.concept_kind == ConceptKind.BUSINESS_ENTITY for item in active),
        "dimensions": sum(item.concept_kind == ConceptKind.DIMENSION for item in active),
        "metrics": sum(item.concept_kind == ConceptKind.METRIC for item in active),
        "relations": sum(item.item_type == "relation" for item in active),
        "changed": changed,
    }
    compilation = Compilation(
        id="cmp_" + fingerprint(f"{view.view_id}:{COMPILER_VERSION}")[:40],
        actor=actor, view_id=view.view_id, view_name=view.name[:256], version=view.version,
        view_fingerprint=view.fingerprint, counts=counts,
    )
    old = await repository.latest("compilations", compilation.id, Compilation)
    return await repository.save(
        "compilations", compilation, expected_revision=old.revision if old else 0
    )


async def compile_after_publish(view_id: str, user: dict) -> None:
    """Called once a Semantic View version is published; never undoes the publication."""
    from app.modules.ontology.repository import ontology_repository
    from app.modules.ontology.representations import SemanticViewCatalog

    try:
        view = await SemanticViewCatalog().published(view_id)
        if view is not None:
            await compile_view(view, Actor.from_user(user), ontology_repository)
    except Exception as exc:  # noqa: BLE001 - the derived layer is rebuilt on the next compile
        logger.warning("Ontology derivation failed for %s: %s", view_id, type(exc).__name__)


# --- read -------------------------------------------------------------------------

async def visible_layer(user: dict, catalog, repository) -> dict:
    """The derived items the caller may see, with official links and entity roles.

    Evidence from a view the caller cannot see is removed; an item left without
    evidence is hidden, and a relation is shown only when both ends are.
    """
    from app.modules.ontology import access
    from app.modules.ontology.contracts import Concept, Domain, Representation

    items = [item for item in await repository.current("derived", DerivedItem)
             if item.status == "ACTIVE"]
    views = sorted({e.semantic.view_id for item in items for e in item.evidence})
    allowed = {view for view in views if await catalog.visible(view, user)}
    shown: dict[str, dict] = {}
    for item in items:
        evidence = [e for e in item.evidence if e.semantic.view_id in allowed]
        if not evidence:
            continue
        # Names and meaning come only from views the caller can see.
        shown[item.key] = {
            **item.model_dump(mode="json", exclude={"actor", "evidence", "confidence"}),
            **_texts(evidence),
            "evidence": [e.model_dump(mode="json") for e in evidence],
            "confidence": _confidence(evidence),
        }
    for key in [k for k, value in shown.items() if value["item_type"] == "relation"]:
        if shown[key]["source_key"] not in shown or shown[key]["target_key"] not in shown:
            shown.pop(key)
    roles: dict[str, set[str]] = {}
    for value in shown.values():
        if value["relation_type"] == RelationType.RELATED_TO.value and value["role"]:
            roles.setdefault(value["target_key"], set()).add(value["role"])
    domains = {item.id: item for item in await repository.current("domains", Domain)}
    concepts = {item.id: item for item in await repository.current("concepts", Concept)}
    official: dict[tuple[str, str], str] = {}
    for binding in await repository.current("representations", Representation):
        concept = concepts.get(binding.concept_id)
        if binding.status != "VALIDATED" or concept is None or concept.status != "APPROVED":
            continue
        domain = domains.get(concept.domain_id)
        if domain is None or not access.can_see(domain, domains, user):
            continue
        official[(binding.semantic.view_id, binding.metric)] = (
            access.qualified_path(domain, domains) + "." + concept.slug
        )
    for value in shown.values():
        value["roles"] = sorted(roles.get(value["key"], set()))
        metric = value.get("metric") or ""
        view_id, _, name = metric.rpartition(".")
        value["official_concept"] = official.get((view_id, name)) if metric else None
    compilations = [
        item.model_dump(mode="json", exclude={"actor"})
        for item in await repository.current("compilations", Compilation)
        if item.view_id in allowed
    ]
    return {
        "items": sorted(shown.values(), key=lambda value: (value["item_type"], value["key"])),
        "compilations": compilations,
        "compiler_version": COMPILER_VERSION,
    }
