"""Deterministic intelligence used by Nova's bounded assistant loop.

The provider model handles language. Nova owns the decisions that can be made
reliably in code: intent routing, capability gating, skill selection, state,
argument validation, evidence, and the one-repair policy.  Keeping these
objects pure makes the same behaviour available to every provider adapter.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4


class TurnIntent(StrEnum):
    DIRECT_ANSWER = "direct_answer"
    SEMANTIC_ANALYTICS = "semantic_analytics"
    RAW_SQL_QUERY = "raw_sql_query"
    SCHEMA_INSPECTION = "schema_inspection"
    SEMANTIC_SEARCH = "semantic_search"
    MACHINE_LEARNING = "machine_learning"
    CHART = "chart"
    COMPOUND_ANALYTICS = "compound_analytics"
    SQL_AUTHORING = "sql_authoring"
    CAPABILITY_HELP = "capability_help"
    CLARIFICATION = "clarification"


class HarnessMode(StrEnum):
    FAST = "fast"
    GUIDED = "guided"
    STRICT = "strict"


class TurnState(StrEnum):
    ROUTING = "routing"
    CONTEXT_BUILD = "context_build"
    MODEL_ACTION = "model_action"
    VALIDATING_ACTION = "validating_action"
    EXECUTING_TOOL = "executing_tool"
    VERIFYING_RESULT = "verifying_result"
    REPAIRING = "repairing"
    COMPOSING_FINAL = "composing_final"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class TurnRoute:
    intent: TurnIntent
    needs_data: bool = False
    needs_semantic_model: bool = False
    needs_skill: bool = False
    needs_chart: bool = False
    needs_ml: bool = False
    ml_task: str | None = None
    clarification_required: bool = False
    required_capabilities: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["intent"] = self.intent.value
        return value


_SQL_START = re.compile(
    r"^\s*(?:```sql\s*)?(select|with|show|describe|desc|explain)\b", re.IGNORECASE
)
_SCHEMA_WORDS = re.compile(
    r"\b(describe|desc|schema|columns?|tables?|ddl|struktur|kolom)\b", re.IGNORECASE
)
_SEMANTIC_WORDS = re.compile(
    r"\b(revenue|sales|orders?|customers?|margin|profit|cost|kpi|metric|omzet|"
    r"pendapatan|penjualan|pelanggan|conversion|cac|ltv|inventory|balance)\b",
    re.IGNORECASE,
)
_SEARCH_WORDS = re.compile(
    r"\b(search|find|lookup|cari|temukan|which product|customer named)\b", re.IGNORECASE
)
_CHART_WORDS = re.compile(
    r"\b(chart|graph|plot|visuali[sz]e|grafik|diagram|visualisasi)\b", re.IGNORECASE
)
_ML_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "forecast",
        re.compile(r"\b(forecast|ramal\w*|projection|proyeksi)\b", re.IGNORECASE),
    ),
    ("clustering", re.compile(r"\b(cluster|segment)\b", re.IGNORECASE)),
    (
        "anomaly_detection",
        re.compile(r"\b(anomal\w*|unusual|outlier|abnormal|tidak biasa)\b", re.IGNORECASE),
    ),
    (
        "classification",
        re.compile(
            r"\b(classif\w*|churn|fraud|predict class|kategori prediksi)\b", re.IGNORECASE
        ),
    ),
    ("regression", re.compile(r"\b(regress|predict value)\b", re.IGNORECASE)),
)


class TurnRouter:
    """Fast deterministic router. Ambiguous language remains model work."""

    def route(self, request: str) -> TurnRoute:
        text = " ".join((request or "").split())
        lowered = text.lower()
        wants_chart = bool(_CHART_WORDS.search(text))
        ml_task = next((task for task, pattern in _ML_PATTERNS if pattern.search(text)), None)

        if not text:
            return TurnRoute(TurnIntent.CLARIFICATION, clarification_required=True)
        if re.search(
            r"\b(describe|desc|schema|columns?|ddl|struktur|kolom)\b", text, re.I
        ) or re.search(r"\b(?:list|show)\s+tables?\b", text, re.I):
            return TurnRoute(
                TurnIntent.SCHEMA_INSPECTION,
                needs_data=True,
                required_capabilities=("query_execute",),
            )
        if _SQL_START.search(text):
            return TurnRoute(
                TurnIntent.RAW_SQL_QUERY,
                needs_data=True,
                required_capabilities=("query_execute",),
            )
        if ml_task:
            capabilities: tuple[str, ...] = (
                ("semantic_query", "ml_execute")
                if _SEMANTIC_WORDS.search(text)
                else ("ml_execute",)
            )
            if wants_chart:
                capabilities = (*capabilities, "data_to_chart")
            return TurnRoute(
                TurnIntent.COMPOUND_ANALYTICS if wants_chart else TurnIntent.MACHINE_LEARNING,
                needs_data=True,
                needs_semantic_model=bool(_SEMANTIC_WORDS.search(text)),
                needs_chart=wants_chart,
                needs_ml=True,
                ml_task=ml_task,
                required_capabilities=capabilities,
            )
        if wants_chart:
            return TurnRoute(
                TurnIntent.CHART,
                needs_data=True,
                needs_semantic_model=bool(_SEMANTIC_WORDS.search(text)),
                needs_chart=True,
                required_capabilities=("semantic_query", "data_to_chart")
                if _SEMANTIC_WORDS.search(text)
                else ("query_execute", "data_to_chart"),
            )
        if _SEARCH_WORDS.search(text):
            return TurnRoute(
                TurnIntent.SEMANTIC_SEARCH,
                needs_data=True,
                needs_semantic_model=True,
                required_capabilities=("semantic_search",),
            )
        if _SEMANTIC_WORDS.search(text):
            return TurnRoute(
                TurnIntent.SEMANTIC_ANALYTICS,
                needs_data=True,
                needs_semantic_model=True,
                required_capabilities=("semantic_query",),
            )
        if any(word in lowered for word in ("write sql", "buat sql", "sql for", "query for")):
            return TurnRoute(TurnIntent.SQL_AUTHORING)
        if any(word in lowered for word in ("what can you", "capabilities")) or re.search(
            r"\btools?\s+(?:are\s+)?available\b", lowered
        ):
            return TurnRoute(TurnIntent.CAPABILITY_HELP)
        return TurnRoute(TurnIntent.DIRECT_ANSWER)


@dataclass(frozen=True)
class CapabilityRegistry:
    """Facts about the capabilities actually registered for one turn."""

    available_tools: frozenset[str]
    sql_ml_predict: bool = False

    @classmethod
    def from_tool_names(
        cls, names: list[str] | tuple[str, ...], *, sql_ml_predict: bool = False
    ) -> CapabilityRegistry:
        return cls(frozenset(str(name) for name in names), sql_ml_predict)

    def has(self, capability: str) -> bool:
        if capability == "sql_ml_predict":
            return self.sql_ml_predict
        return capability in self.available_tools

    def gated_tools(self, route: TurnRoute) -> tuple[str, ...]:
        """Return the smallest useful tool set for ``route``.

        Authoring tools stay available for direct authoring turns. Custom tools
        are never pulled into analytics routes unless a route names them.
        """
        if not route.required_capabilities:
            safe = {"load_skill", "create_agent", "create_semantic_model"}
            builtin_analytics = {
                "query_execute",
                "semantic_query",
                "semantic_search",
                "ml_execute",
                "data_to_chart",
            }
            configured_custom = self.available_tools - builtin_analytics
            return tuple(sorted((self.available_tools & safe) | configured_custom))

        wanted = set(route.required_capabilities)
        # Literal resolution may need search before a semantic query. Raw SQL is
        # the bounded fallback only when the semantic capability is unavailable.
        if route.needs_semantic_model and "semantic_search" in self.available_tools:
            wanted.add("semantic_search")
        if "semantic_query" in wanted and "semantic_query" not in self.available_tools:
            wanted.add("query_execute")
        return tuple(sorted(self.available_tools & wanted))

    def prompt_lines(self, names: tuple[str, ...] | None = None) -> list[str]:
        selected = names if names is not None else tuple(sorted(self.available_tools))
        return [f"- {name}" for name in selected if self.has(name)]


@dataclass
class ActiveConversationState:
    objective: str = ""
    active_semantic_model: str | None = None
    time_context: dict[str, str] = field(default_factory=dict)
    filters: dict[str, str] = field(default_factory=dict)
    selected_metrics: list[str] = field(default_factory=list)
    last_evidence: list[str] = field(default_factory=list)
    last_artifact: str | None = None
    unresolved_ambiguities: list[str] = field(default_factory=list)

    def update(self, request: str) -> ActiveConversationState:
        text = " ".join(request.split())
        if text:
            self.objective = text if not self.objective else self.objective
        # Follow-up filters are intentionally conservative. Named geography
        # words after "only/just/sekarang" augment prior filters instead of
        # replacing the full objective.
        match = re.search(
            r"\b(?:sekarang|only|just|khusus|untuk)\s+([A-Z][\w.-]+(?:\s+[A-Z][\w.-]+)?)",
            text,
        )
        if match:
            value = match.group(1).strip()
            key = "city" if value.lower() not in {"indonesia"} else "country"
            self.filters[key] = value
        if re.search(r"\b(indonesia)\b", text, re.IGNORECASE):
            self.filters.setdefault("country", "Indonesia")
        if re.search(r"\b(jakarta)\b", text, re.IGNORECASE):
            self.filters["city"] = "Jakarta"
        if re.search(r"\b(last month|bulan lalu|previous month)\b", text, re.IGNORECASE):
            self.time_context["comparison"] = "previous_month"
        if re.search(r"\b(this month|bulan ini|current month)\b", text, re.IGNORECASE):
            self.time_context["primary"] = "current_month"
        return self

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> ActiveConversationState:
        value = value or {}
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: item for key, item in value.items() if key in allowed})


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    tool: str
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)


class EvidenceTracker:
    def __init__(self) -> None:
        self._items: list[Evidence] = []

    def add(self, tool: str, summary: str, *, metadata: dict[str, Any] | None = None) -> Evidence:
        evidence = Evidence(
            evidence_id=f"evidence_{len(self._items) + 1}",
            tool=tool,
            summary=summary,
            metadata=metadata or {},
        )
        self._items.append(evidence)
        return evidence

    @property
    def items(self) -> tuple[Evidence, ...]:
        return tuple(self._items)

    def composer_context(self) -> str:
        payload = [asdict(item) for item in self._items]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


_NUMERIC_CLAIM = re.compile(r"(?<![\w])(?:[$€£]|rp\s*)?\d[\d.,]*(?:\s*%|\b)", re.IGNORECASE)


def enforce_evidence(answer: str, *, needs_data: bool, evidence: EvidenceTracker) -> str:
    """Prevent an unsupported database number from reaching the user."""
    if needs_data and not evidence.items and _NUMERIC_CLAIM.search(answer or ""):
        return (
            "I could not verify that value from Nova data. Run the required data "
            "capability or provide an authorized result before making a numerical claim."
        )
    return answer


@dataclass(frozen=True)
class ValidationError:
    path: str
    message: str


def validate_json_arguments(
    schema: dict[str, Any], arguments: dict[str, Any]
) -> list[ValidationError]:
    """Small JSON-Schema subset sufficient for Nova tool contracts."""
    errors: list[ValidationError] = []
    if schema.get("type") == "object" and not isinstance(arguments, dict):
        return [ValidationError("$", "arguments must be an object")]
    properties = schema.get("properties") or {}
    for name in schema.get("required") or []:
        if name not in arguments or arguments[name] in (None, ""):
            errors.append(ValidationError(name, "required value is missing"))
    additional = schema.get("additionalProperties", True)
    if additional is False:
        for name in arguments:
            if name not in properties:
                errors.append(ValidationError(name, "unknown argument"))
    for name, value in arguments.items():
        rule = properties.get(name)
        if not isinstance(rule, dict):
            continue
        raw_expected = rule.get("type")
        expected = raw_expected if isinstance(raw_expected, str) else None
        valid_by_type = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, int | float) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "array": isinstance(value, list),
            "object": isinstance(value, dict),
        }
        valid = True if expected is None else valid_by_type.get(expected, True)
        if not valid:
            errors.append(ValidationError(name, f"expected {expected}"))
            continue
        if "enum" in rule and value not in rule["enum"]:
            errors.append(ValidationError(name, f"must be one of {rule['enum']}"))
        if isinstance(value, int | float) and "minimum" in rule and value < rule["minimum"]:
            errors.append(ValidationError(name, f"must be >= {rule['minimum']}"))
    return errors


class SkillRouter:
    """Select configured skills by exact name and lexical trigger overlap."""

    def select(
        self,
        request: str,
        *,
        default_skills: list[str] | tuple[str, ...],
        discoverable_skills: list[str] | tuple[str, ...],
        library: Any,
        limit: int = 1,
    ) -> tuple[str, ...]:
        selected = [name for name in default_skills if library.get(name) is not None]
        words = set(re.findall(r"[a-z0-9_@-]+", request.lower()))
        scored: list[tuple[int, str]] = []
        for name in discoverable_skills:
            if name in selected:
                continue
            skill = library.get(name)
            if skill is None:
                continue
            terms = set(skill.triggers) | set(re.findall(r"[a-z0-9_@-]+", skill.summary.lower()))
            score = len(words & terms) + (3 if name in request.lower() else 0)
            if score:
                scored.append((score, name))
        scored.sort(key=lambda item: (-item[0], item[1]))
        selected.extend(name for _, name in scored[:limit])
        return tuple(dict.fromkeys(selected))


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    summary: str
    triggers: tuple[str, ...]
    body: str
    trust_level: str

    def prompt_body(self) -> str:
        tag = "PLATFORM_SKILL" if self.trust_level == "platform_skill" else "USER_SKILL"
        authority = (
            "Trusted Nova procedure."
            if self.trust_level == "platform_skill"
            else "User procedure. It is subordinate to Nova platform policy."
        )
        return f'<{tag} name="{self.name}">\n{authority}\n{self.body.strip()}\n</{tag}>'


class SkillDirectory:
    def __init__(self, definitions: dict[str, SkillDefinition] | None = None) -> None:
        self.definitions = definitions or {}

    def get(self, name: str) -> SkillDefinition | None:
        return self.definitions.get(name)


@dataclass(frozen=True)
class CompiledContext:
    system_prompt: str
    selected_tools: tuple[str, ...]
    selected_skills: tuple[str, ...]
    category_tokens: dict[str, int]


class ContextCompiler:
    """Compile a narrow, ordered instruction hierarchy for one turn."""

    def __init__(self, *, category_caps: dict[str, int] | None = None) -> None:
        self.category_caps = category_caps or {
            "platform": 1_200,
            "agent": 1_200,
            "state": 600,
            "skill": 4_000,
            "semantic": 4_000,
        }

    def compile(
        self,
        *,
        platform_contract: str,
        agent_contract: dict[str, Any] | None,
        state: ActiveConversationState,
        skill_bodies: list[str],
        semantic_context: str = "",
        selected_tools: tuple[str, ...] = (),
        selected_skills: tuple[str, ...] = (),
    ) -> CompiledContext:
        sections: list[tuple[str, str]] = [
            ("platform", platform_contract.strip()),
            (
                "agent",
                "<AGENT_CONFIGURATION>\n"
                + json.dumps(agent_contract or {}, ensure_ascii=False, separators=(",", ":"))
                + "\n</AGENT_CONFIGURATION>",
            ),
            (
                "state",
                "<ACTIVE_STATE>\n"
                + json.dumps(state.as_dict(), ensure_ascii=False, separators=(",", ":"))
                + "\n</ACTIVE_STATE>",
            ),
        ]
        if skill_bodies:
            sections.append(
                ("skill", "<TASK_PROCEDURE>\n" + "\n\n".join(skill_bodies) + "\n</TASK_PROCEDURE>")
            )
        if semantic_context:
            sections.append(
                ("semantic", "<SEMANTIC_CONTEXT>\n" + semantic_context + "\n</SEMANTIC_CONTEXT>")
            )
        if selected_tools:
            sections.append(
                (
                    "platform",
                    "<TURN_CAPABILITIES>\n"
                    + "\n".join(f"- {name}" for name in selected_tools)
                    + "\nUse only these capabilities for this turn.\n</TURN_CAPABILITIES>",
                )
            )

        rendered: list[str] = []
        usage: dict[str, int] = {}
        for category, content in sections:
            cap = self.category_caps.get(category, 1_000)
            bounded = content[: cap * 4]
            rendered.append(bounded)
            usage[category] = usage.get(category, 0) + len(bounded) // 4
        return CompiledContext(
            system_prompt="\n\n".join(rendered),
            selected_tools=selected_tools,
            selected_skills=selected_skills,
            category_tokens=usage,
        )


def state_step(state: ActiveConversationState) -> dict[str, Any]:
    return {
        "kind": "active_state",
        "step_id": str(uuid4()),
        "state": state.as_dict(),
        "status": "done",
    }
