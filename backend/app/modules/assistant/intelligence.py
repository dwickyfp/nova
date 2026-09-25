"""Deterministic validation, state, and evidence for Nova's bounded assistant loop."""

from __future__ import annotations

import json
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
    UI_OPERATION = "ui_operation"
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
    needs_diagnosis: bool = False
    ml_task: str | None = None
    clarification_required: bool = False
    required_capabilities: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["intent"] = self.intent.value
        return value


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

    def prompt_lines(self, names: tuple[str, ...] | None = None) -> list[str]:
        selected = names if names is not None else tuple(sorted(self.available_tools))
        return [f"- {name}" for name in selected if self.has(name)]


@dataclass
class ActiveConversationState:
    objective: str = ""
    surface_id: str | None = None
    active_semantic_model: str | None = None
    time_context: dict[str, str] = field(default_factory=dict)
    filters: dict[str, str] = field(default_factory=dict)
    selected_metrics: list[str] = field(default_factory=list)
    last_evidence: list[str] = field(default_factory=list)
    last_artifact: str | None = None
    unresolved_ambiguities: list[str] = field(default_factory=list)
    semantic_plan: dict[str, Any] = field(default_factory=dict)
    intent_revision: int = 0
    intent_changes: list[dict[str, Any]] = field(default_factory=list)

    def update(self, request: str) -> ActiveConversationState:
        if request.strip() and not self.objective:
            self.objective = " ".join(request.split())[:2000]
        if request.strip():
            self.intent_revision += 1
        return self

    def merge_patch(self, patch: dict[str, Any] | None) -> ActiveConversationState:
        """Merge a tool-owned semantic state patch with explicit field semantics."""
        if not isinstance(patch, dict):
            return self
        previous = {
            "selected_metrics": list(self.selected_metrics),
            "filters": dict(self.filters),
            "time_context": dict(self.time_context),
        }
        if isinstance(patch.get("semantic_plan"), dict):
            self.semantic_plan = dict(patch["semantic_plan"])
        scalar_fields = {"objective", "active_semantic_model", "last_artifact"}
        for name in scalar_fields:
            value = patch.get(name)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            setattr(self, name, str(value))
        for name in ("time_context", "filters"):
            value = patch.get(name)
            if isinstance(value, dict):
                target = getattr(self, name)
                if isinstance(patch.get("semantic_plan"), dict):
                    target.clear()
                for key, item in value.items():
                    if item is None:
                        target.pop(str(key), None)
                    else:
                        target[str(key)[:256]] = str(item)[:2000]
        for name in ("selected_metrics", "last_evidence", "unresolved_ambiguities"):
            value = patch.get(name)
            if isinstance(value, list | tuple):
                setattr(self, name, list(dict.fromkeys(str(item) for item in value))[-20:])
        for field_name, old_value in previous.items():
            new_value = getattr(self, field_name)
            if old_value == new_value:
                continue
            self.intent_changes.append(
                {
                    "revision": self.intent_revision,
                    "field": field_name,
                    "previous": old_value,
                    "current": dict(new_value) if isinstance(new_value, dict) else list(new_value),
                }
            )
        self.intent_changes = self.intent_changes[-30:]
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
        self._tables: dict[str, dict[str, Any]] = {}

    def add(
        self,
        tool: str,
        summary: str,
        *,
        metadata: dict[str, Any] | None = None,
        table: dict[str, Any] | None = None,
    ) -> Evidence:
        evidence = Evidence(
            evidence_id=f"evidence_{len(self._items) + 1}",
            tool=tool,
            summary=summary,
            metadata=metadata or {},
        )
        self._items.append(evidence)
        if (
            tool in {"semantic_query", "semantic_view_query", "query_execute", "diagnose_change"}
            and table
        ):
            self._tables[evidence.evidence_id] = {
                "columns": list(table.get("columns") or [])[:100],
                "rows": list(table.get("rows") or [])[:200],
            }
            if len(self._tables) > 8:
                self._tables.pop(next(iter(self._tables)))
        return evidence

    @property
    def items(self) -> tuple[Evidence, ...]:
        return tuple(self._items)

    @property
    def tables(self) -> dict[str, dict[str, Any]]:
        return dict(self._tables)

    def composer_context(self) -> str:
        payload = [asdict(item) for item in self._items]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class TurnEvidenceRequirements:
    requires_database_evidence: bool = False
    required_capabilities: tuple[str, ...] = ()
    allowed_evidence_ids: tuple[str, ...] = ()


def enforce_evidence(answer: str, *, needs_data: bool, evidence: EvidenceTracker) -> str:
    """Prevent any data conclusion when a data-routed turn has no evidence.

    Numeric detection remains useful defense in depth, but routing owns the
    primary decision: qualitative claims are database claims too.
    """
    if needs_data and not evidence.items and (answer or "").strip():
        return (
            "I could not verify that conclusion from authorized Nova data. "
            "The required data capability did not produce evidence, so no factual "
            "business-data answer was returned."
        )
    return answer


@dataclass(frozen=True)
class ValidationError:
    path: str
    message: str


def validate_json_arguments(
    schema: dict[str, Any], arguments: dict[str, Any]
) -> list[ValidationError]:
    """Validate the same bounded schema sent to the provider, recursively."""
    return _validate_json_value(schema, arguments, "", 0)


def _validate_json_value(
    schema: dict[str, Any], value: Any, path: str, depth: int
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    if depth > 32:
        return [ValidationError(path or "$", "value exceeds nesting limit")]
    valid_types = {
        "null": value is None,
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, int | float) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }
    expected = schema.get("type")
    types = [expected] if isinstance(expected, str) else expected
    if types and not any(valid_types.get(kind, False) for kind in types):
        return [ValidationError(path or "$", f"expected {expected}")]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(ValidationError(path or "$", "value is outside enum"))
    if isinstance(value, int | float) and not isinstance(value, bool):
        for key, failed in (
            ("minimum", value < schema.get("minimum", value)),
            ("maximum", value > schema.get("maximum", value)),
        ):
            if failed:
                errors.append(ValidationError(path or "$", f"value violates {key}"))
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors.extend(
                _validate_json_value(schema["items"], item, f"{path}[{index}]", depth + 1)
            )
    if not isinstance(value, dict):
        return errors
    arguments = value
    properties = schema.get("properties") or {}
    for name in schema.get("required") or []:
        if name not in arguments:
            errors.append(
                ValidationError(f"{path}.{name}".lstrip("."), "required value is missing")
            )
    additional = schema.get("additionalProperties", True)
    if additional is False:
        for name in arguments:
            if name not in properties:
                errors.append(ValidationError(f"{path}.{name}".lstrip("."), "unknown argument"))
    for name, value in arguments.items():
        rule = properties.get(name, additional if isinstance(additional, dict) else None)
        if not isinstance(rule, dict):
            continue
        errors.extend(_validate_json_value(rule, value, f"{path}.{name}".lstrip("."), depth + 1))
    return errors


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
