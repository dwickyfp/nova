"""An agent's harness: the components around the model, validated as one surface.

The harness of a Studio agent is everything that shapes how the shared engine
works for it. Each component has a cost and a reach:

========================  ==============  ===========================================
component                 loaded          stored in
========================  ==============  ===========================================
instructions              always          ``instructions_response`` / ``_orchestration``
standing rules            always          ``harness_policy.standing_rules``
tool guidance             always          ``harness_policy.tool_guidance``
skills                    on demand       ``default_skills`` / ``discoverable_skills``
procedures                on demand       custom tools selected in ``default_tools``
guards                    in the loop     platform guards + ``harness_policy.guards``
memory                    retrieved       ``CONFIG_AGENT_MEMORIES`` (top 8, bounded)
budget                    every turn      ``budget_profile`` / ``budget_seconds``
========================  ==============  ===========================================

Always-on text competes with the agent's own output budget on every turn, so it
is capped. A guard is the only component the model cannot skip. The validator
reports every problem at once, in prose that can be fed back to a proposer.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.agents.instructions import (
    _FORBIDDEN_OVERRIDES,
    compile_agent_record,
    lint_instruction_sources,
)
from app.modules.assistant.skills import contains_credential_shape
from app.modules.assistant.tools.redaction import is_credential_value

#: Always-on harness text (standing rules + tool guidance). The compiled agent
#: section has its own cap; this one keeps the harness section from being cut.
ALWAYS_ON_HARNESS_CHARS = 4_000
#: The agent contract section of the system prompt is cut beyond this size.
AGENT_CONTRACT_CHARS = 4_800
MAX_STANDING_RULES = 12
MAX_TOOL_GUIDANCE = 16
MAX_GUARDS = 16
PLATFORM_GUARDS = (
    {
        "rule_id": "untrusted-instructions-in-data",
        "stage": "after_tool",
        "description": "Labels result values that try to instruct the agent and audits them.",
    },
    {
        "rule_id": "credential-in-answer",
        "stage": "answer",
        "description": "Removes credential-shaped values from the final answer.",
    },
)

_RULE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,47}$")
_TOOL_NAME = re.compile(r"^(?:\*|[A-Za-z][A-Za-z0-9_]{1,127})$")


def _screen(text: str, where: str) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        raise ValueError(f"{where} is empty")
    for pattern, reason in _FORBIDDEN_OVERRIDES:
        if pattern.search(cleaned):
            raise ValueError(f"{where}: {reason}")
    if contains_credential_shape(cleaned) or is_credential_value(cleaned):
        raise ValueError(f"{where} must not contain a credential")
    return cleaned


class HarnessGuardRule(BaseModel):
    """One declarative guard. Data only: phrases and a message, never code."""

    rule_id: str = Field(min_length=2, max_length=48)
    stage: Literal["before_tool", "after_tool_error"]
    tool: str = Field(min_length=1, max_length=128)
    match_any: list[str] = Field(min_length=1, max_length=12)
    message: str = Field(min_length=8, max_length=600)

    @field_validator("rule_id")
    @classmethod
    def _rule_id(cls, value: str) -> str:
        if not _RULE_ID.match(value):
            raise ValueError("rule_id uses lowercase letters, digits, '-' or '_'")
        return value

    @field_validator("tool")
    @classmethod
    def _tool(cls, value: str) -> str:
        if not _TOOL_NAME.match(value):
            raise ValueError("tool is a registered tool name or '*'")
        return value

    @field_validator("match_any")
    @classmethod
    def _phrases(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            phrase = " ".join(str(value or "").split()).lower()
            if not 2 <= len(phrase) <= 120:
                raise ValueError("each phrase is 2 to 120 characters")
            cleaned.append(phrase)
        return list(dict.fromkeys(cleaned))

    @field_validator("message")
    @classmethod
    def _message(cls, value: str) -> str:
        return _screen(value, "guard message")


class HarnessPolicy(BaseModel):
    """The editable harness components stored on the agent row."""

    standing_rules: list[str] = Field(default_factory=list, max_length=MAX_STANDING_RULES)
    tool_guidance: dict[str, str] = Field(default_factory=dict)
    guards: list[HarnessGuardRule] = Field(default_factory=list, max_length=MAX_GUARDS)

    @field_validator("standing_rules")
    @classmethod
    def _rules(cls, values: list[str]) -> list[str]:
        cleaned = []
        for index, value in enumerate(values):
            rule = _screen(value, f"standing rule {index + 1}")
            if len(rule) > 300:
                raise ValueError(f"standing rule {index + 1} is over 300 characters")
            cleaned.append(rule)
        return list(dict.fromkeys(cleaned))

    @field_validator("tool_guidance")
    @classmethod
    def _guidance(cls, values: dict[str, str]) -> dict[str, str]:
        if len(values) > MAX_TOOL_GUIDANCE:
            raise ValueError(f"at most {MAX_TOOL_GUIDANCE} tools have guidance")
        cleaned = {}
        for name, text in values.items():
            if name == "*" or not _TOOL_NAME.match(name):
                raise ValueError(f"tool guidance key {name!r} is not a tool name")
            guidance = _screen(text, f"guidance for {name}")
            if len(guidance) > 600:
                raise ValueError(f"guidance for {name} is over 600 characters")
            cleaned[name] = guidance
        return cleaned

    @model_validator(mode="after")
    def _surface(self) -> HarnessPolicy:
        ids = [rule.rule_id for rule in self.guards]
        duplicates = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
        if duplicates:
            raise ValueError(f"guard rule ids repeat: {', '.join(duplicates)}")
        reserved = {item["rule_id"] for item in PLATFORM_GUARDS} & set(ids)
        if reserved:
            raise ValueError(f"guard rule ids are reserved by the platform: {sorted(reserved)}")
        size = always_on_chars(self)
        if size > ALWAYS_ON_HARNESS_CHARS:
            raise ValueError(
                f"standing rules and tool guidance are {size} characters, over the "
                f"{ALWAYS_ON_HARNESS_CHARS} cap. They are in context on every turn; move a "
                "procedure into a skill or a check into a guard, or cut something."
            )
        return self


def always_on_chars(policy: HarnessPolicy) -> int:
    return sum(len(rule) for rule in policy.standing_rules) + sum(
        len(name) + len(text) for name, text in policy.tool_guidance.items()
    )


def parse_policy(raw: Any) -> HarnessPolicy:
    """The stored policy, or an empty one when a stored row is missing or invalid."""
    if isinstance(raw, HarnessPolicy):
        return raw
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return HarnessPolicy()
    if not isinstance(raw, dict):
        return HarnessPolicy()
    try:
        return HarnessPolicy.model_validate(raw)
    except ValueError:
        return HarnessPolicy()


def harness_prompt(policy: Any, tools: list[str] | tuple[str, ...]) -> str:
    """The always-on harness section; tool guidance only for tools this turn has."""
    parsed = parse_policy(policy)
    lines: list[str] = []
    if parsed.standing_rules:
        lines.append("Standing rules from the agent owner (platform policy still wins):")
        lines += [f"- {rule}" for rule in parsed.standing_rules]
    available = set(tools)
    guidance = [(name, text) for name, text in parsed.tool_guidance.items() if name in available]
    if guidance:
        lines.append("Tool guidance:")
        lines += [f"- {name}: {text}" for name, text in guidance]
    return "\n".join(lines)


def enabled_tool_names(agent: dict[str, Any]) -> set[str]:
    """Tool names this agent registers, without contacting external servers."""
    from app.modules.agents.registry import build_registry

    names = set(build_registry(agent).names())
    names.add("describe_agent")
    if "load_skill" in names:
        names.add("load_personal_skill")
    for selected in agent.get("default_tools") or []:
        text = str(selected)
        # Registered names of owner procedures and connector tools.
        if text.startswith("custom:"):
            names.add("custom_" + text.split(":", 1)[1])
        elif text.startswith("mcp:"):
            names.add("mcp_" + text.split(":", 1)[1].replace("-", ""))
    return names


def validate_harness(
    agent: dict[str, Any], *, strict: bool = False, tools: set[str] | None = None,
) -> list[dict[str, str]]:
    """Every problem with this agent's harness, as ``{severity, component, message}``.

    ``strict`` is for automatically proposed harnesses: a reference to a tool the
    agent does not have is dead weight and fails the proposal, as in Harness².
    """
    issues: list[dict[str, str]] = []

    def add(severity: str, component: str, message: str) -> None:
        issues.append({"severity": severity, "component": component, "message": message})

    try:
        policy = HarnessPolicy.model_validate(agent.get("harness_policy") or {})
    except ValueError as exc:
        add("error", "harness_policy", str(exc))
        policy = HarnessPolicy()
    available = tools if tools is not None else enabled_tool_names(agent)
    dead = "error" if strict else "warning"
    for name in policy.tool_guidance:
        if name not in available:
            add(dead, "tool_guidance", f"guidance for '{name}', which this agent does not have")
    for rule in policy.guards:
        if rule.tool != "*" and rule.tool not in available:
            add(dead, "guards", f"guard '{rule.rule_id}' targets '{rule.tool}', which this "
                "agent does not have")
    contract = compile_agent_record(agent)
    contract_chars = len(json.dumps(contract, ensure_ascii=False, separators=(",", ":")))
    if contract_chars > AGENT_CONTRACT_CHARS:
        add("warning", "instructions",
            f"compiled instructions are {contract_chars} characters; the prompt keeps "
            f"{AGENT_CONTRACT_CHARS}. Move procedures into skills.")
    for rejected in contract.get("rejected_rules") or []:
        add("warning", "instructions", f"rule kept out of the prompt: {str(rejected)[:160]}")
    for warning in lint_instruction_sources(
        str(agent.get("instructions_response") or ""),
        str(agent.get("instructions_orchestration") or ""),
        *policy.standing_rules,
    ):
        add("warning", "instructions", warning)
    if strict:
        for name in agent.get("default_skills") or []:
            if not str(name).strip():
                add("error", "skills", "an empty skill name is selected")
    return issues


def harness_surface(agent: dict[str, Any], *, memory_count: int | None = None) -> dict[str, Any]:
    """A census of every component, its load cost, and the validation report."""
    from app.modules.agents.service import loop_limits

    policy = parse_policy(agent.get("harness_policy"))
    tools = enabled_tool_names(agent)
    contract = compile_agent_record(agent)
    instructions_chars = len(str(agent.get("instructions_response") or "")) + len(
        str(agent.get("instructions_orchestration") or "")
    )
    limits = loop_limits(agent)
    procedures = sorted(name for name in tools if name.startswith("custom_"))
    return {
        "components": [
            {"key": "instructions", "load": "always", "count": len(contract.get("must_do") or [])
             + len(contract.get("must_not") or []) + len(contract.get("scope") or []),
             "chars": instructions_chars},
            {"key": "standing_rules", "load": "always", "count": len(policy.standing_rules),
             "chars": sum(len(rule) for rule in policy.standing_rules)},
            {"key": "tool_guidance", "load": "always", "count": len(policy.tool_guidance),
             "chars": sum(len(text) for text in policy.tool_guidance.values())},
            {"key": "skills", "load": "on_demand",
             "count": len(agent.get("default_skills") or [])
             + len(agent.get("discoverable_skills") or []),
             "default": list(agent.get("default_skills") or []),
             "discoverable": list(agent.get("discoverable_skills") or [])},
            {"key": "procedures", "load": "on_demand", "count": len(procedures),
             "names": procedures},
            {"key": "guards", "load": "in_loop",
             "count": len(PLATFORM_GUARDS) + len(policy.guards),
             "platform": list(PLATFORM_GUARDS),
             "agent": [rule.model_dump() for rule in policy.guards]},
            {"key": "memory", "load": "retrieved", "count": memory_count},
            {"key": "budget", "load": "every_turn", "profile": agent.get("budget_profile"),
             "max_iterations": limits.max_iterations,
             "time_budget_seconds": limits.time_budget_seconds,
             "max_calls_per_tool": limits.max_calls_per_tool,
             "spend_tokens": limits.spend_tokens},
        ],
        "always_on": {
            "harness_chars": always_on_chars(policy),
            "harness_cap": ALWAYS_ON_HARNESS_CHARS,
            "contract_chars": len(json.dumps(contract, ensure_ascii=False,
                                             separators=(",", ":"))),
            "contract_cap": AGENT_CONTRACT_CHARS,
        },
        "tools": sorted(tools),
        "policy": policy.model_dump(),
        "issues": validate_harness(agent, tools=tools),
    }
