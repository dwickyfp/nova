"""Names and definitions for approved concepts, suggested by Nova, decided by stewards.

People ask in their own words: "sopir", "toko", "penjualan bersih". The
resolver only matches names an approved concept carries, so every missing
everyday word is a question Studio cannot ground. This fills that gap from two
sources:

- **Derived roles, deterministic.** An entity that relations use as
  ``driver`` or ``salesman`` gets those names as aliases.
- **A language model, validated.** One structured call per domain proposes
  everyday Indonesian and English aliases, a definition where a concept has
  only a placeholder, and existing names that could mean another concept. It
  sees names, definitions and formulas only, never data values.

Nothing the model says is trusted. The validator keeps only suggestions for
concepts that were sent, drops a name another visible concept already uses or
two concepts were given, drops generic words, numbers and causal claims, and
bounds sizes. What survives becomes one ``ENGINE`` proposal per domain, so a
steward approves every word before a release carries it.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from pydantic import Field

from app.modules.intelligence.contracts import Contract, fingerprint
from app.modules.ontology import access
from app.modules.ontology.contracts import (
    Concept,
    ConceptBody,
    ConceptChange,
    ConceptKind,
    Proposal,
)
from app.modules.ontology.derived import visible_layer
from app.modules.ontology.resolver import names_of, normalise
from app.modules.ontology.stewardship import ProposalCreate, StewardshipService, load_state

MAX_PARALLEL_CALLS = 3
MAX_CONCEPTS = 15  # per model call; small batches keep each call well inside its timeout
MAX_NEW_ALIASES = 6
MAX_ALIAS_CHARS = 40
MAX_ALIAS_WORDS = 4
PLACEHOLDER = re.compile(r"^derived by nova\b|steward should confirm", re.IGNORECASE)
CAUSAL = re.compile(
    r"\b(menyebabkan|mengakibatkan|akibat|karena|sebab|cause[sd]?|because|due to|"
    r"drives?|leads? to|results? in)\b",
    re.IGNORECASE,
)
GENERIC = {
    "data", "nilai", "jumlah", "total", "angka", "value", "amount", "count", "number",
    "metric", "metrik", "laporan", "report", "info", "informasi", "hasil", "result",
}

SYSTEM = (
    "You edit a business glossary for an Indonesian company. For each concept you get "
    "its qualified name, kind, label, definition, current names and, for metrics, the "
    "formula. Return the words business people in Indonesia actually type for that "
    "exact meaning: everyday Bahasa Indonesia first, then common English, 1 to 4 words "
    "each, at most 5 per concept. Prefer fewer words, or none, over a coined or literal "
    "translation nobody says. Never use the name of a related but different measure "
    "(sell-through is not sell-out; ordered is not planned for delivery). For an entity "
    "whose names include roles, give the everyday word for each role too. Do not repeat "
    "current names, do not invent a broader or narrower meaning than the formula "
    "computes, and do not reuse a word for two concepts. "
    "Write a definition (one sentence, at most 300 characters, in the language of the "
    "existing names) only when the current definition is empty or says it was derived "
    "by Nova; otherwise return an empty string. Never explain causes. List current "
    "names that could equally mean another listed concept, with a short reason. Use "
    "only the qualified names you were given. Return JSON matching the schema."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["concepts"],
    "properties": {
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "aliases", "definition", "ambiguous"],
                "properties": {
                    "name": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "definition": {"type": "string"},
                    "ambiguous": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "reason"],
                            "properties": {
                                "name": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                        },
                    },
                },
            },
        }
    },
}


class EnrichRequest(Contract):
    operation_id: str = Field(min_length=8, max_length=64)
    #: One domain, or every visible domain.
    domain_id: str | None = Field(default=None, max_length=64)


@dataclass
class Suggestion:
    concept_id: str
    aliases: list[tuple[str, str]] = field(default_factory=list)  # (alias, source)
    definition: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class Rejection:
    concept: str
    value: str
    reason: str


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split()).strip(" .,;:'\"")


def validate(
    raw: Any,
    sent: dict[str, Concept],
    names: dict[str, str],
    taken: dict[str, set[str]],
) -> tuple[dict[str, Suggestion], list[Rejection]]:
    """Keep only what is safe to propose.

    ``sent`` maps the qualified names sent to the model to their concepts;
    ``names`` maps concept ids to qualified names; ``taken`` maps every
    normalised name of a visible approved concept to the concept ids using it.
    """
    accepted: dict[str, Suggestion] = {}
    rejected: list[Rejection] = []
    rows = raw.get("concepts") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return accepted, [Rejection("", "", "The model returned no concept list")]
    claimed: dict[str, set[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        qualified = str(row.get("name") or "")
        concept = sent.get(qualified)
        if concept is None:
            rejected.append(Rejection(qualified[:200], "", "Not a concept that was sent"))
            continue
        suggestion = accepted.setdefault(concept.id, Suggestion(concept.id))
        own = names_of(concept, qualified)
        for value in row.get("aliases") or []:
            alias = _clean(value)
            key = normalise(alias)
            reason = None
            if not key or key in own:
                continue
            if len(alias) > MAX_ALIAS_CHARS or len(alias.split()) > MAX_ALIAS_WORDS:
                reason = "Longer than an everyday name"
            elif key.replace(" ", "").isdigit() or len(key) < 2:
                reason = "Not a name"
            elif key in GENERIC:
                reason = "Too generic to mean one concept"
            elif CAUSAL.search(alias):
                reason = "States a cause"
            elif taken.get(key, set()) - {concept.id}:
                others = sorted(names[cid] for cid in taken[key] - {concept.id} if cid in names)
                reason = "Already names " + (", ".join(others) or "another concept")
            if reason:
                rejected.append(Rejection(qualified, alias, reason))
                continue
            claimed.setdefault(key, set()).add(concept.id)
            suggestion.aliases.append((alias, "model"))
        definition = _clean(row.get("definition"))
        if definition:
            if not (PLACEHOLDER.search(concept.definition) or len(concept.definition) < 20):
                rejected.append(Rejection(qualified, definition[:120],
                                          "The concept already has a definition"))
            elif CAUSAL.search(definition):
                rejected.append(Rejection(qualified, definition[:120], "States a cause"))
            elif not 20 <= len(definition) <= 400:
                rejected.append(Rejection(qualified, definition[:120],
                                          "A definition is one sentence of 20-400 characters"))
            else:
                suggestion.definition = definition + ("" if definition.endswith(".") else ".")
        for item in row.get("ambiguous") or []:
            if not isinstance(item, dict):
                continue
            name, why = _clean(item.get("name")), _clean(item.get("reason"))[:200]
            if normalise(name) in own and why and not CAUSAL.search(why):
                suggestion.notes.append(f"'{name}' may also mean: {why}")
    # A word suggested for two concepts means neither.
    for key, owners in claimed.items():
        if len(owners) < 2:
            continue
        for cid in owners:
            kept = []
            for alias, source in accepted[cid].aliases:
                if normalise(alias) == key:
                    rejected.append(Rejection(names[cid], alias, "Suggested for two concepts"))
                else:
                    kept.append((alias, source))
            accepted[cid].aliases = kept
    for suggestion in accepted.values():
        suggestion.aliases = suggestion.aliases[:MAX_NEW_ALIASES]
    return accepted, rejected


def _formula(concept_id: str, state, views: dict) -> str | None:
    for binding in state.representations.values():
        if binding.concept_id != concept_id or binding.status != "VALIDATED":
            continue
        model = views.get(binding.semantic.view_id)
        metric = model.metric(binding.metric) if model else None
        if metric is not None:
            return metric.expression[:400]
    return None


async def _ask(client, catalog_rows: list[dict]) -> dict:
    """One structured call, repaired once. Provider errors propagate to the caller."""
    from app.modules.agents.semantic.model_planner import parse_json_object

    config = await client.resolve()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps({"concepts": catalog_rows}, ensure_ascii=False)},
    ]
    kwargs: dict[str, Any] = {"provider": config}
    if config.capabilities.supports_json_schema:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "ontology_names", "strict": True, "schema": SCHEMA},
        }
    else:
        messages[0]["content"] += " Schema: " + json.dumps(SCHEMA)
    for attempt in range(2):
        message = await client.complete(messages=messages, **kwargs)
        content = message.get("content") or ""
        try:
            parsed = parse_json_object(content)
        except Exception:  # noqa: BLE001 - unreadable output is simply no suggestion
            parsed = {}
        if isinstance(parsed.get("concepts"), list) or attempt:
            return parsed
        # One repair, as the Semantic View planner does; then unreadable means nothing.
        messages = [
            *messages,
            {"role": "assistant", "content": str(content)[:2000]},
            {"role": "user",
             "content": "Return only one JSON object with a 'concepts' list matching the schema."},
        ]
    return {}


async def enrich(
    body: EnrichRequest, user: dict, stewardship: StewardshipService, client=None
) -> dict:
    from app.modules.agents.semantic.ir import SemanticModelIR

    if client is None:
        from app.modules.assistant.provider import AssistantProviderClient

        client = AssistantProviderClient(timeout_seconds=180, max_attempts=1)
    repository, catalog = stewardship.repository, stewardship.catalog
    state = await load_state(repository)
    domains = state.domains
    visible = {
        domain_id for domain_id, domain in domains.items()
        if domain.status == "ACTIVE" and access.can_see(domain, domains, user)
    }
    if body.domain_id is not None and body.domain_id not in visible:
        raise HTTPException(status_code=404, detail=access.NOT_FOUND)
    targets = [body.domain_id] if body.domain_id else sorted(visible)
    concepts = {
        cid: concept for cid, concept in state.concepts.items()
        if concept.status == "APPROVED" and concept.domain_id in visible
    }
    names = {
        cid: access.qualified_path(domains[c.domain_id], domains) + "." + c.slug
        for cid, c in concepts.items()
    }
    taken: dict[str, set[str]] = {}
    for cid, concept in concepts.items():
        for name in names_of(concept, names[cid]):
            taken.setdefault(name, set()).add(cid)
    busy: set[str] = set()  # concepts an open proposal already changes
    for proposal in await repository.current("proposals", Proposal):
        if proposal.status not in {"PENDING", "APPROVED"}:
            continue
        for change in proposal.changes:
            if not isinstance(change, ConceptChange):
                continue
            if change.op != "create":
                busy.add(change.concept_id)
            # A name an open proposal gives a concept is taken until it is decided.
            if change.body is not None:
                owner = change.concept_id or f"pending:{proposal.id}"
                for name in (change.body.slug, change.body.label, *change.body.aliases):
                    if key := normalise(name):
                        taken.setdefault(key, set()).add(owner)

    layer = await visible_layer(user, catalog, repository)
    items = {item["key"]: item for item in layer["items"]}
    # A synonym a view gives one official metric is that metric's word, even before a
    # steward copied it onto the concept.
    by_name = {name: cid for cid, name in names.items()}
    for item in items.values():
        holder = by_name.get(item.get("official_concept") or "")
        if holder is None:
            continue
        for alias in item["aliases"]:
            if key := normalise(alias):
                taken.setdefault(key, set()).add(holder)
    view_ids = {binding.semantic.view_id for binding in state.representations.values()
                if binding.concept_id in concepts and binding.status == "VALIDATED"}
    models: dict[str, Any] = {}
    for view_id in sorted(view_ids):
        if not await catalog.visible(view_id, user):
            continue
        view = await catalog.published(view_id)
        if view is not None:
            try:
                models[view_id] = SemanticModelIR.from_ossie(view.definition)
            except Exception:  # noqa: BLE001 - an unreadable view sends no formula
                continue

    created: list[Proposal] = []
    rejected: list[Rejection] = []
    plans: list[tuple[str, list[Concept], dict[str, Suggestion], list[list[Concept]]]] = []
    prompts: list[list[dict]] = []
    for domain_id in targets:
        own = sorted(
            (c for cid, c in concepts.items() if c.domain_id == domain_id and cid not in busy),
            key=lambda c: names[c.id],
        )
        if not own:
            continue
        suggestions: dict[str, Suggestion] = {}
        # Derived roles become names, deterministically.
        for concept in own:
            if concept.kind != ConceptKind.BUSINESS_ENTITY:
                continue
            for item in items.values():
                if item["concept_kind"] != "BUSINESS_ENTITY" or (
                    item["label"].casefold() != concept.label.casefold()
                ):
                    continue
                for role in item["roles"]:
                    key = normalise(role)
                    if key and key not in taken:
                        suggestions.setdefault(concept.id, Suggestion(concept.id)).aliases.append(
                            (role, "relation role")
                        )
                        taken[key] = {concept.id}
        batches = [own[start:start + MAX_CONCEPTS] for start in range(0, len(own), MAX_CONCEPTS)]
        for batch in batches:
            rows = []
            for concept in batch:
                row: dict[str, Any] = {
                    "name": names[concept.id], "kind": concept.kind.value,
                    "label": concept.label, "definition": concept.definition[:600],
                    "names": sorted({*concept.aliases, concept.slug})[:20],
                }
                if concept.unit:
                    row["unit"] = concept.unit
                formula = _formula(concept.id, state, models)
                if formula:
                    row["formula"] = formula
                role_names = [a for a, _ in suggestions.get(concept.id, Suggestion("")).aliases]
                if role_names:
                    row["names"] = sorted({*row["names"], *role_names})
                rows.append(row)
            prompts.append(rows)
        plans.append((domain_id, own, suggestions, batches))

    # Every call runs together, a few at a time; a batch the provider cannot answer
    # is reported, and only a run where no batch answered fails.
    from app.modules.assistant.provider import AssistantProviderError

    gate = asyncio.Semaphore(MAX_PARALLEL_CALLS)

    async def ask(rows: list[dict]) -> dict | AssistantProviderError:
        async with gate:
            try:
                return await _ask(client, rows)
            except AssistantProviderError as exc:
                return exc

    answers = list(await asyncio.gather(*(ask(rows) for rows in prompts)))
    failures = [answer for answer in answers if isinstance(answer, AssistantProviderError)]
    if answers and len(failures) == len(answers):
        # The provider error is safe to show: a status and a generic reason, never a key.
        raise HTTPException(
            status_code=503,
            detail=f"The AI provider could not suggest names: {failures[0].message}",
        )

    for domain_id, own, suggestions, batches in plans:
        # Validation runs in order, so a word one batch keeps is taken for the next.
        for batch in batches:
            raw = answers.pop(0)
            if isinstance(raw, AssistantProviderError):
                rejected.extend(
                    Rejection(names[c.id], "", "The model did not answer in time; run again")
                    for c in batch
                )
                continue
            accepted, dropped = validate(
                raw, {names[c.id]: c for c in batch}, names, taken,
            )
            rejected.extend(dropped)
            for cid, suggestion in accepted.items():
                merged = suggestions.setdefault(cid, Suggestion(cid))
                merged.aliases.extend(suggestion.aliases)
                merged.definition = suggestion.definition
                merged.notes.extend(suggestion.notes)
                for alias, _source in suggestion.aliases:
                    taken.setdefault(normalise(alias), set()).add(cid)

        changes: list = []
        lines: list[str] = []
        for concept in own:
            found = suggestions.get(concept.id)
            if found is None:
                continue
            suggestion = found
            existing = {normalise(alias) for alias in concept.aliases}
            fresh: list[tuple[str, str]] = []
            for alias, source in suggestion.aliases:
                key = normalise(alias)
                if key not in existing and len(concept.aliases) + len(fresh) < 32:
                    existing.add(key)
                    fresh.append((alias, source))
            if not fresh and not suggestion.definition:
                if suggestion.notes:
                    lines.append(f"- {names[concept.id]}: " + "; ".join(suggestion.notes))
                continue
            current = concept.model_dump(include=set(ConceptBody.model_fields))
            current["aliases"] = [*concept.aliases, *(alias for alias, _ in fresh)]
            if suggestion.definition:
                current["definition"] = suggestion.definition
            changes.append(ConceptChange(
                op="update", concept_id=concept.id, base_revision=concept.revision,
                body=ConceptBody(**current),
            ))
            parts = []
            by_source: dict[str, list[str]] = {}
            for alias, source in fresh:
                by_source.setdefault(source, []).append(alias)
            for source, values in by_source.items():
                parts.append(
                    "+ " + ", ".join(f"'{value}'" for value in values) + f" ({source})"
                )
            if suggestion.definition:
                parts.append("new definition (model)")
            parts.extend(suggestion.notes)
            lines.append(f"- {names[concept.id]}: " + "; ".join(parts))
        if not changes:
            continue
        domain = domains[domain_id]
        rationale = (
            f"Nova suggests the everyday names people use for {len(changes)} concept(s) in "
            f"{domain.name}. Relation roles come from the derived layer; the rest from a "
            "language model, kept only after validation (no name another concept uses, no "
            "generic word, no causal claim). Check each word means exactly this concept.\n"
            + "\n".join(lines)
        )[:4000]
        digest = fingerprint([change.model_dump(mode="json") for change in changes])
        created.append(await stewardship.create(
            ProposalCreate(
                title=f"Names and definitions for {domain.name}",
                rationale=rationale,
                changes=changes,
                operation_id="enr-" + fingerprint([body.operation_id, domain_id, digest])[:40],
            ),
            user,
            source="ENGINE",
        ))
    await access.audit(
        "ENRICH", "ONTOLOGY_PROPOSAL", ",".join(p.id for p in created)[:128] or "none", user,
    )
    return {
        "proposals": [p.model_dump(mode="json", exclude={"request_digest"}) for p in created],
        "rejected": [
            {"concept": item.concept, "value": item.value, "reason": item.reason}
            for item in rejected
        ][:200],
    }
