"""Search packaged Nova references without reading user files or contacting services."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.modules.assistant.app_context import NoveAppContext
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.skill_registry import skill_library
from app.modules.assistant.skills import contains_credential_shape
from app.modules.assistant.tools import ToolInvocation, ToolOutcome

_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge_library"
_STOP_WORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "is",
        "are",
        "in",
        "of",
        "to",
        "for",
        "and",
        "or",
        "how",
        "what",
        "nova",
        "itu",
        "apa",
        "di",
        "dan",
        "untuk",
        "dengan",
        "bagaimana",
    ]
)
_CONTEXT_ONLY_WORDS = frozenset(
    {
        "this", "that", "it", "here", "these", "does", "do", "did", "why",
        "work", "works", "fail", "failing", "explain", "help", "ini", "tersebut",
    }
)
_SURFACE_REFERENCES: dict[str, tuple[tuple[str, int], ...]] = {
    "workspace": (("query-troubleshooting", 5), ("data-workspace-catalog", 4)),
    "role": (("security-governance", 6), ("stages-tasks-access", 2)),
    "roles": (("security-governance", 6),),
    "studio": (("nove-workflows", 6), ("ml-ai-providers", 5)),
    "semantic": (("intelligence-semantic-features", 6),),
    "intelligence": (("intelligence-semantic-features", 5),),
    "monitoring": (("operations-monitoring", 6), ("query-troubleshooting", 2)),
    "query-history": (("operations-monitoring", 6), ("query-troubleshooting", 2)),
}


def _surface_boosts(app_context: NoveAppContext | None) -> dict[str, int]:
    if app_context is None:
        return {}
    keys = {
        app_context.surface.id.split(".", 1)[0].replace("_", "-"),
    }
    if app_context.entity is not None:
        keys.add(app_context.entity.type.replace("_", "-"))
        keys.add(app_context.entity.type.split("_", 1)[0])
    boosts: dict[str, int] = {}
    for key in keys:
        for source, weight in _SURFACE_REFERENCES.get(key, ()):
            reference = f"knowledge:{source}"
            boosts[reference] = max(boosts.get(reference, 0), weight)
    execution = app_context.execution
    if execution is not None and execution.type in {"sql", "query"} and execution.status == "error":
        boosts["knowledge:query-troubleshooting"] = max(
            boosts.get("knowledge:query-troubleshooting", 0), 8
        )
    return boosts


def search_references(
    query: str, *, app_context: NoveAppContext | None = None
) -> list[dict[str, str]]:
    terms = set(re.findall(r"[\w@-]+", query.lower())) - _STOP_WORDS
    boosts = _surface_boosts(app_context)
    if not terms and not boosts:
        return []
    contextual_query = bool(boosts) and (not terms or terms <= _CONTEXT_ONLY_WORDS)
    match_terms = set() if contextual_query else terms
    references = [
        (
            f"skill:{skill.name}",
            f"{skill.title} {skill.summary} {' '.join(skill.triggers)}",
            skill.body,
        )
        for skill in skill_library.skills
    ]
    references.extend(
        (f"knowledge:{path.stem}", path.stem.replace("-", " "), path.read_text(encoding="utf-8"))
        for path in sorted(_KNOWLEDGE_DIR.glob("*.md"))
    )
    ranked: list[tuple[int, str, str]] = []
    for source, title, body in references:
        if contains_credential_shape(body):
            continue
        title_words = set(re.findall(r"[\w@-]+", title.lower()))
        body_words = set(re.findall(r"[\w@-]+", body.lower()))
        score = 3 * len(match_terms & title_words) + len(match_terms & body_words)
        keywords = re.search(r"(?im)^Keywords:\s*(.+)$", body)
        if keywords and not contextual_query:
            for alias in keywords.group(1).split(","):
                alias = alias.strip(" .").lower()
                if alias and re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", query.lower()):
                    score += 8 * len(alias.split())
        surface_bonus = boosts.get(source, 0)
        if score:
            score += min(surface_bonus, 2)
        elif contextual_query:
            score = surface_bonus
        if score:
            ranked.append((score, source, body))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [
        {
            "source": source,
            "revision": hashlib.sha256(body.encode()).hexdigest()[:16],
            "text": _answer_text(body)[:3500],
            "truncated": str(len(_answer_text(body)) > 3500).lower(),
        }
        for _, source, body in ranked[:3]
    ]


def _answer_text(body: str) -> str:
    return re.split(r"(?im)^Implementation (?:references|sources):", body, maxsplit=1)[0].rstrip()


def reference_passages(references: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"text": reference["text"]} for reference in references]


class SearchKnowledgeTool:
    name = "search_knowledge"
    description = (
        "Search packaged Nova product guidance and SQL playbooks using topic keywords "
        "in English or Indonesian. Returns bounded guidance. These references "
        "describe Nova; they do not prove current "
        "database contents, privileges, deployment configuration, or task status."
    )
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Nova topic or question."}},
        "required": ["query"],
        "additionalProperties": False,
    }
    classification: ToolClassification = "read_only"
    requires_consent = False

    def preview(self, invocation: ToolInvocation) -> str:
        return "Search Nova reference documentation"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        query = invocation.arguments.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            return ToolOutcome(
                ok=False, summary="", error="Provide a topic of 1 to 1000 characters."
            )
        app_context = getattr(context, "app_context", None)
        references = search_references(
            query,
            app_context=app_context if isinstance(app_context, NoveAppContext) else None,
        )
        return ToolOutcome(
            ok=True,
            summary="<NOVA_REFERENCE_DATA>\n"
            + json.dumps(
                {
                    "references": reference_passages(references),
                    "runtime_evidence": False,
                    "instruction": (
                        "Use this guidance to answer in your own words without source "
                        "identifiers or implementation paths. Verify runtime facts "
                        "with authorized tools."
                    ),
                },
                ensure_ascii=False,
            )
            + "\n</NOVA_REFERENCE_DATA>",
            metadata={"kind": "documentation", "sources": [r["source"] for r in references]},
        )


search_knowledge_tool = SearchKnowledgeTool()
