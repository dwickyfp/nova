"""Contracts for the shared News edition built from a Semantic View.

An edition is produced once per view by the role's scheduled execution account.
A reader never receives a story because of who produced it: each story carries
access proofs, and a story is shown only when the reader reproduces every proof
through their own governed query.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from app.modules.intelligence.contracts import (
    Confidence,
    Contract,
    Record,
    SemanticRef,
    Window,
)

#: One governed query returns at most this many rows.
MAX_PROOF_ROWS = 1000


class NewsConfig(Contract):
    version: Literal[1] = 1
    execution_role: str = Field(min_length=1, max_length=128)
    #: Server-derived from the role's execution binding; never caller-supplied.
    execution_user: str | None = Field(default=None, max_length=128)
    metrics: list[str] = Field(min_length=1, max_length=3)
    count_metric: str = Field(min_length=1, max_length=128)
    slice_dimensions: list[str] = Field(default_factory=list, max_length=3)
    time_dimension: str = Field(min_length=1, max_length=128)
    timezone: str = Field(default="Asia/Jakarta", max_length=128)
    baseline_weeks: int = Field(default=4, ge=2, le=8)
    relative_threshold: float = Field(default=0.1, gt=0, le=10)
    absolute_threshold: float = Field(default=0, ge=0)
    minimum_samples: int = Field(default=30, ge=2)
    max_slice_values: int = Field(default=30, ge=1, le=100)
    max_stories: int = Field(default=12, ge=1, le=24)
    cadence_minutes: int = Field(default=60, ge=15, le=1440)
    narrative: Literal["template", "model"] = "model"

    @model_validator(mode="after")
    def bounded(self):
        if len(set(self.metrics)) != len(self.metrics):
            raise ValueError("Each News metric can be listed once")
        if len(set(self.slice_dimensions)) != len(self.slice_dimensions):
            raise ValueError("Each News dimension can be listed once")
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError) as exc:
            raise ValueError("Use a named IANA timezone") from exc
        if self.max_slice_values * self.days > MAX_PROOF_ROWS:
            raise ValueError(
                "Lower the slice limit or the baseline weeks; one News query reads at most "
                f"{MAX_PROOF_ROWS} rows"
            )
        return self

    @property
    def days(self) -> int:
        """Days read per query: the edition day and every baseline week before it."""
        return 7 * self.baseline_weeks + 1


class NewsSettings(Contract):
    enabled: bool
    config: NewsConfig | None = None


class ProofGroup(Contract):
    """One governed daily query; every reader runs all groups of an edition."""

    key: str = Field(min_length=1, max_length=64)
    metric: str = Field(min_length=1, max_length=128)
    dimension: str | None = Field(default=None, max_length=128)
    plan: dict[str, Any]


class Edition(Record):
    semantic: SemanticRef
    edition_date: date
    window: Window
    groups: list[ProofGroup] = Field(default_factory=list, max_length=12)
    nonce: str = Field(min_length=32, max_length=64)
    pressed_at: datetime
    config_digest: str = Field(min_length=64, max_length=64)
    story_ids: list[str] = Field(default_factory=list, max_length=24)
    #: Changes whenever any story's proven rows change, so a reader's cached
    #: proof of an earlier pressing is never matched against new stories.
    proofs_digest: str = Field(default="", max_length=64)
    #: Whether a higher value of each metric is better for the business, as the
    #: model judged it from the published definition. Absent means not judged.
    polarity: dict[str, Literal["better", "worse", "neutral"]] = Field(default_factory=dict)
    #: Coverage notes for managers. Never contains story counts or slice values.
    warnings: list[str] = Field(default_factory=list, max_length=12)


class StoryProof(Contract):
    """Rows a reader must reproduce exactly before the story exists for them."""

    group_key: str = Field(min_length=1, max_length=64)
    #: ``total`` and ``group`` cover every row of the query; ``slice`` covers one value.
    scope: Literal["total", "slice", "group"]
    value: str | None = Field(default=None, max_length=256)
    digest: str = Field(min_length=64, max_length=64)
    rows: int = Field(ge=1, le=MAX_PROOF_ROWS)


class StorySlice(Contract):
    dimension: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=256)


class SeriesPoint(Contract):
    date: date
    value: float
    count: int = Field(ge=0)


class Driver(Contract):
    dimension: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=256)
    before: float
    after: float
    change: float


class BusinessRule(Contract):
    source: Literal["metric", "dimension", "view", "threshold"]
    name: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=1000)


class Narrative(Contract):
    headline: str = Field(min_length=1, max_length=160)
    deck: str = Field(min_length=1, max_length=320)
    what_happened: str = Field(min_length=1, max_length=900)
    why_it_matters: str = Field(min_length=1, max_length=900)
    what_to_check: str = Field(min_length=1, max_length=900)


class Story(Record):
    edition_id: str = Field(min_length=1, max_length=64)
    semantic: SemanticRef
    metric: str = Field(min_length=1, max_length=128)
    metric_label: str = Field(min_length=1, max_length=160)
    unit: str | None = Field(default=None, max_length=32)
    slice: StorySlice | None = None
    edition_date: date
    window: Window
    baseline_dates: list[date] = Field(min_length=2, max_length=8)
    before: float
    after: float
    change: float
    relative_change: float | None = None
    severity: Literal["warning", "critical"]
    #: Good or bad for the business; ``None`` when the metric was not judged.
    impact: Literal["favorable", "unfavorable", "neutral"] | None = None
    rank: int = Field(ge=1, le=24)
    confidence: Confidence
    proofs: list[StoryProof] = Field(min_length=1, max_length=4)
    series: list[SeriesPoint] = Field(min_length=1, max_length=64)
    drivers: list[Driver] = Field(default_factory=list, max_length=8)
    narrative: Narrative
    business_rules: list[BusinessRule] = Field(default_factory=list, max_length=6)
    narrative_source: Literal["template", "model"] = "template"
    narrative_model: str | None = Field(default=None, max_length=256)
    dedup_key: str = Field(min_length=64, max_length=64)
