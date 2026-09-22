"""Compile free-form Agent Studio instructions into a bounded contract."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.modules.assistant.skills import contains_credential_shape

MAX_INSTRUCTION_CHARS = 12_000

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_FORBIDDEN_OVERRIDES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b(ignore|bypass|override)\b.{0,40}\b(policy|rules?|guard|security)\b", re.I),
        "platform policy cannot be overridden",
    ),
    (
        re.compile(
            r"\b(show|reveal|emit|return)\b.{0,40}\b(password|token|secret|api key)\b", re.I
        ),
        "secrets cannot be requested or exposed",
    ),
    (
        re.compile(r"\b(no|without|skip)\b.{0,30}\b(consent|approval|authorization)\b", re.I),
        "consent and authorization cannot be disabled",
    ),
)


class InstructionCompilationError(ValueError):
    pass


@dataclass(frozen=True)
class ResponseContract:
    concise: bool = False
    lead_with_answer: bool = True
    tabular: bool = False


@dataclass(frozen=True)
class AgentInstructionContract:
    mission: str
    scope: tuple[str, ...] = ()
    must_do: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()
    preferred_capabilities: tuple[str, ...] = ()
    response_contract: ResponseContract = field(default_factory=ResponseContract)
    rejected_rules: tuple[str, ...] = ()
    version: int = 1

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def compile_agent_instructions(
    *,
    response: str = "",
    orchestration: str = "",
    description: str = "",
    response_style: str | None = None,
) -> AgentInstructionContract:
    """Deterministically normalize legacy instruction fields.

    An LLM-based editor may produce this same schema, but persistence always
    passes through this validator. This path is provider-free and therefore
    works for migrations and unavailable-provider conditions.
    """
    raw = "\n".join(part.strip() for part in (response, orchestration) if part.strip())
    if len(raw) > MAX_INSTRUCTION_CHARS:
        raise InstructionCompilationError(
            f"Agent instructions exceed the {MAX_INSTRUCTION_CHARS}-character limit."
        )
    if contains_credential_shape(raw):
        raise InstructionCompilationError(
            "Agent instructions contain a credential-shaped value. Store secrets in Nova's "
            "configured secret locations, not in agent configuration."
        )

    accepted: list[str] = []
    rejected: list[str] = []
    for sentence in _sentences(raw):
        conflict = next(
            (reason for pattern, reason in _FORBIDDEN_OVERRIDES if pattern.search(sentence)),
            None,
        )
        if conflict:
            rejected.append(f"{sentence} ({conflict})")
        else:
            accepted.append(sentence)

    must_not = [
        sentence
        for sentence in accepted
        if re.search(r"\b(never|must not|do not|don't|avoid|jangan)\b", sentence, re.I)
    ]
    must_do = [
        sentence
        for sentence in accepted
        if sentence not in must_not
        and re.search(
            r"\b(always|must|prefer|use|check|include|compare|pastikan|gunakan)\b", sentence, re.I
        )
    ]
    remaining = [sentence for sentence in accepted if sentence not in must_do + must_not]
    mission = (description or (remaining[0] if remaining else "Assist with Nova data work")).strip()
    scope = _scope_terms(" ".join([description, raw]))
    capabilities = _preferred_capabilities(raw)
    style = (response_style or "").lower()
    concise = style == "concise" or bool(re.search(r"\b(concise|brief|short|ringkas)\b", raw, re.I))
    tabular = style == "tabular" or bool(re.search(r"\b(table|tabular|tabel)\b", raw, re.I))
    lead = not bool(re.search(r"\b(background first|explain first)\b", raw, re.I))
    return AgentInstructionContract(
        mission=mission[:512],
        scope=tuple(scope[:20]),
        must_do=tuple(_dedupe(must_do)[:40]),
        must_not=tuple(_dedupe(must_not)[:40]),
        preferred_capabilities=tuple(capabilities),
        response_contract=ResponseContract(
            concise=concise,
            lead_with_answer=lead,
            tabular=tabular,
        ),
        rejected_rules=tuple(rejected[:20]),
    )


def compile_agent_record(agent: dict[str, Any]) -> dict[str, Any]:
    existing = agent.get("compiled_instructions")
    if isinstance(existing, dict) and existing.get("version") == 1:
        return existing
    return compile_agent_instructions(
        response=str(agent.get("instructions_response") or ""),
        orchestration=str(agent.get("instructions_orchestration") or ""),
        description=str(agent.get("description") or ""),
        response_style=agent.get("response_style"),
    ).as_dict()


def lint_instruction_sources(*sources: str) -> list[str]:
    """Report conflicts that would make a weak model choose unsafe behaviour."""
    joined = "\n".join(sources)
    warnings: list[str] = []
    if re.search(r"never\s+(?:ask for|emit).{0,30}password", joined, re.I) and re.search(
        r"generate.{0,30}(?:random|strong).{0,15}password", joined, re.I
    ):
        warnings.append("password generation conflicts with the no-secret platform policy")
    if re.search(r"if .{0,20}(?:tool|call).{0,20}fails?,?\s*stop", joined, re.I) and re.search(
        r"retry|repair", joined, re.I
    ):
        warnings.append("unqualified stop-on-tool-failure conflicts with bounded repair")
    return warnings


def _sentences(text: str) -> list[str]:
    return [" ".join(part.split()) for part in _SENTENCE.split(text) if part.strip()]


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _scope_terms(text: str) -> list[str]:
    candidates = re.findall(
        r"\b(revenue|sales|finance|marketing|customer|customers|inventory|orders?|"
        r"forecast|anomaly|data quality|semantic metrics?)\b",
        text,
        re.I,
    )
    return _dedupe([item.lower() for item in candidates])


def _preferred_capabilities(text: str) -> list[str]:
    mapping = (
        ("semantic_query", r"\b(semantic|business metric|governed metric|kpi)\b"),
        ("query_execute", r"\b(raw sql|schema inspection|describe table)\b"),
        ("semantic_search", r"\b(search|literal|lookup)\b"),
        ("ml_execute", r"\b(forecast|cluster|anomal|classif|regress|machine learning|ml)\b"),
        ("data_to_chart", r"\b(chart|graph|visuali[sz])\b"),
    )
    return [name for name, pattern in mapping if re.search(pattern, text, re.I)]
