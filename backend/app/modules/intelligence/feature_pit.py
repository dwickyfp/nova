"""Compile a point-in-time feature join without leaking future observations."""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.agents.semantic.expressions import quote_identifier, quote_source


class FeaturePlanError(ValueError):
    """The feature request cannot be compiled safely."""


@dataclass(frozen=True)
class FeatureRelation:
    relation: str
    version: int
    entity_keys: tuple[str, ...]
    event_timestamp: str
    feature_columns: tuple[str, ...]


@dataclass(frozen=True)
class TrainingRelation:
    relation: str
    entity_keys: tuple[str, ...]
    event_timestamp: str
    label_columns: tuple[str, ...]


@dataclass(frozen=True)
class TrainingSetPlan:
    sql: str
    feature_version: int
    source_relations: tuple[str, str]


def compile_training_set(labels: TrainingRelation, features: FeatureRelation) -> TrainingSetPlan:
    """Use StarRocks ASOF LEFT JOIN to select the last feature at or before a label."""
    if not labels.entity_keys or labels.entity_keys != features.entity_keys:
        raise FeaturePlanError("Label and feature entity keys must match in order")
    if not labels.label_columns or not features.feature_columns:
        raise FeaturePlanError("Training data needs a label and at least one feature")
    if features.version < 1:
        raise FeaturePlanError("Feature version must be positive")
    if len(labels.entity_keys) > 8 or len(features.feature_columns) > 128:
        raise FeaturePlanError("Too many entity keys or feature columns")

    try:
        label_relation = quote_source(labels.relation)
        feature_relation = quote_source(features.relation)
        all_columns = (
            *labels.entity_keys,
            labels.event_timestamp,
            *labels.label_columns,
            features.event_timestamp,
            *features.feature_columns,
        )
        for column in all_columns:
            quote_identifier(column)
    except ValueError as exc:
        raise FeaturePlanError("Invalid training relation or column") from exc

    if len({key.casefold() for key in labels.entity_keys}) != len(labels.entity_keys):
        raise FeaturePlanError("Entity keys must be unique")
    if len({column.casefold() for column in labels.label_columns}) != len(labels.label_columns):
        raise FeaturePlanError("Label columns must be unique")
    if len({column.casefold() for column in features.feature_columns}) != len(
        features.feature_columns
    ):
        raise FeaturePlanError("Feature columns must be unique")
    label_names = {
        column.casefold()
        for column in (*labels.entity_keys, labels.event_timestamp, *labels.label_columns)
    }
    if len(label_names) != len(labels.entity_keys) + 1 + len(labels.label_columns):
        raise FeaturePlanError("Label columns must not repeat keys or event time")
    feature_keys = {key.casefold() for key in features.entity_keys}
    if features.event_timestamp.casefold() in feature_keys or any(
        column.casefold() in label_names
        or column.casefold() == features.event_timestamp.casefold()
        for column in features.feature_columns
    ):
        raise FeaturePlanError("Feature columns must not repeat output or temporal columns")

    label_projection = [
        f"l.{quote_identifier(column)} AS {quote_identifier(column)}"
        for column in (*labels.entity_keys, labels.event_timestamp, *labels.label_columns)
    ]
    feature_projection = [
        f"f.{quote_identifier(column)} AS {quote_identifier(column)}"
        for column in features.feature_columns
    ]
    equality = " AND ".join(
        f"l.{quote_identifier(key)} = f.{quote_identifier(key)}" for key in labels.entity_keys
    )
    asof = (
        f"l.{quote_identifier(labels.event_timestamp)} >= "
        f"f.{quote_identifier(features.event_timestamp)}"
    )
    sql = (
        "SELECT "
        + ", ".join((*label_projection, *feature_projection))
        + f" FROM {label_relation} AS l ASOF LEFT JOIN {feature_relation} AS f "
        + f"ON {equality} AND {asof}"
    )
    return TrainingSetPlan(sql, features.version, (labels.relation, features.relation))
