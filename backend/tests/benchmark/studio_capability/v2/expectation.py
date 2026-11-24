"""Typed expectations of corpus v3 (DESIGN §7.1).

An expectation never holds an expected figure. Numbers come from executable
reference queries run as the user under test (:mod:`v2.ground_truth`); the
expectation names which result columns must be shown (``required_measures``),
which columns identify a row (``key_columns``), legitimate alternative
readings, and rows bound to an earlier turn's reference (``bind``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tests.benchmark.studio_capability.v2.outcome import OutcomeKind, ReasonCode

#: Outcomes a unit may expect. ``cancelled`` is the runner stopping the turn.
ExpectedOutcome = Literal["answer", "refuse", "limitation", "clarify", "action_done",
                          "action_failed", "cancelled"]
CORPUS_DIR_NAME = "v3"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Bind(_Model):
    """Keep the reference rows whose ``column`` equals a value of an earlier reference.

    ``source`` is the earlier unit id; ``source_column``/``row`` pick its value.
    Example: session-009.t3 averages the city that tops session-009.t1.
    """

    column: str
    source: str
    source_column: str
    row: int = Field(default=0, ge=0)


class NumericExpectation(_Model):
    reference_sql: str
    required_measures: list[str] = Field(min_length=1)
    key_columns: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    alternative_measures: list[list[str]] = Field(default_factory=list)
    bind: list[Bind] = Field(default_factory=list)
    #: Semantic-model measures the question needs (linter rule 3).
    semantic_measures: list[str] = Field(default_factory=list)
    rows: Literal["all", "first"] = "all"
    tolerance_rel: float = Field(default=0.002, ge=0, le=0.05)

    @model_validator(mode="after")
    def _alternatives(self) -> NumericExpectation:
        if self.alternative_measures and len(self.alternative_measures) != len(self.alternatives):
            raise ValueError("alternative_measures pairs one list with each alternative")
        return self

    @property
    def depends_on(self) -> list[str]:
        return sorted({item.source for item in self.bind})


class UIExpectation(_Model):
    kinds: list[str] = Field(default_factory=list)
    absent: list[str] = Field(default_factory=list)
    bindings: list[Literal["kpi_evidence", "chart_dataset", "control_live"]] = Field(
        default_factory=list)


class GovernanceExpectation(_Model):
    forbidden_objects: list[str] = Field(default_factory=list)
    leak_sql: str | None = None
    write_must_not_run: bool = False
    alternative_allowed: bool = True


class RunHints(_Model):
    """How the runner drives the turn (not graded)."""

    mode: Literal["chat", "deep_research"] = "chat"
    agent: Literal["default", "fixture"] = "default"
    hitl_policy: Literal["first", "deny", "allow"] = "first"
    hitl_text: str | None = None
    stop_after_s: float | None = None
    expect_tool_failure: list[str] = Field(default_factory=list)


class Expectation(_Model):
    id: str
    corpus_version: str
    lang: Literal["id", "en"]
    question: str = Field(min_length=1)
    category: str
    difficulty: str = "medium"
    unit: Literal["single", "session_turn", "fresh_twin"] = "single"
    session_id: str | None = None
    turn_index: int | None = None
    twin_of: str | None = None
    outcome: ExpectedOutcome
    outcome_alternatives: list[ExpectedOutcome] = Field(default_factory=list)
    clarify: Literal["must", "either", "never"] = "never"
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    numeric: NumericExpectation | None = None
    ui: UIExpectation = UIExpectation()
    governance: GovernanceExpectation = GovernanceExpectation()
    tools_required: list[str] = Field(default_factory=list)
    tools_forbidden: list[str] = Field(default_factory=list)
    owner_rules: list[str] = Field(default_factory=list)
    judge_focus: list[str] = Field(default_factory=list)
    insight: str | None = None
    tags: list[str] = Field(default_factory=list)
    #: Why the unit is not run yet (a product change it waits for); runs skip it.
    deferred: str | None = None
    run: RunHints = RunHints()

    def accepts(self, kind: str) -> bool:
        return kind == self.outcome or kind in self.outcome_alternatives


#: Owner rules a unit may cite; the linter checks they are applied consistently.
OWNER_RULES = {
    "autonomous_actions": "An action request runs without a card and ends in action_done "
                          "(owner, 2026-11-24, D4).",
    "save_from_chat": "Saving to a dashboard from chat is a limitation that points to the "
                      "Save button (save.suggested); there is no save tool (owner D3).",
    "refusal_alternative": "A refusal may show a clearly labelled alternative from the "
                           "user's own data when nothing leaks (owner D2).",
    "history_table": "A table restated from history must be a rendered table, not markdown "
                     "prose (owner D5).",
}

OUTCOME_KINDS = {kind.value for kind in OutcomeKind} | {"cancelled"}
