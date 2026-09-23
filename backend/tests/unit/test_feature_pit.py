"""Point-in-time compiler must never select a later feature observation."""

import pytest

from app.modules.intelligence.feature_pit import (
    FeaturePlanError,
    FeatureRelation,
    TrainingRelation,
    compile_training_set,
)

LABELS = TrainingRelation(
    relation="commerce.labels",
    entity_keys=("tenant_id", "customer_id"),
    event_timestamp="event_time",
    label_columns=("churned",),
)
FEATURES = FeatureRelation(
    relation="commerce.customer_features_v3",
    version=3,
    entity_keys=("tenant_id", "customer_id"),
    event_timestamp="available_at",
    feature_columns=("orders_7d", "revenue_30d"),
)


def test_compiles_compound_identity_and_temporal_condition():
    plan = compile_training_set(LABELS, FEATURES)
    assert "ASOF LEFT JOIN" in plan.sql
    assert "l.`tenant_id` = f.`tenant_id` AND l.`customer_id` = f.`customer_id`" in plan.sql
    assert "l.`event_time` >= f.`available_at`" in plan.sql
    assert plan.feature_version == 3
    assert plan.source_relations == ("commerce.labels", "commerce.customer_features_v3")


@pytest.mark.parametrize(
    "feature",
    [
        FeatureRelation("commerce.f", 1, ("customer_id",), "t", ("x",)),
        FeatureRelation("commerce.f", 1, (), "t", ("x",)),
        FeatureRelation("commerce.f", 0, LABELS.entity_keys, "t", ("x",)),
        FeatureRelation("commerce.f", 1, LABELS.entity_keys, "t", ()),
        FeatureRelation("commerce.f", 1, LABELS.entity_keys, "t", ("x", "x")),
        FeatureRelation("commerce.f", 1, LABELS.entity_keys, "t", ("customer_id",)),
    ],
)
def test_rejects_incompatible_feature_contract(feature):
    with pytest.raises(FeaturePlanError):
        compile_training_set(LABELS, feature)


def test_rejects_unsafe_relation():
    unsafe = FeatureRelation("commerce.f;DROP TABLE users", 1, LABELS.entity_keys, "t", ("x",))
    with pytest.raises(FeaturePlanError):
        compile_training_set(LABELS, unsafe)


def test_rejects_oversized_and_repeated_entity_keys():
    too_many = tuple(f"key_{index}" for index in range(9))
    labels = TrainingRelation("commerce.labels", too_many, "event_time", ("churned",))
    features = FeatureRelation("commerce.features", 1, too_many, "available_at", ("value",))
    with pytest.raises(FeaturePlanError, match="Too many entity keys"):
        compile_training_set(labels, features)

    repeated = ("customer_id", "CUSTOMER_ID")
    labels = TrainingRelation("commerce.labels", repeated, "event_time", ("churned",))
    features = FeatureRelation("commerce.features", 1, repeated, "available_at", ("value",))
    with pytest.raises(FeaturePlanError, match="Entity keys must be unique"):
        compile_training_set(labels, features)


@pytest.mark.parametrize(
    "labels,features",
    [
        (
            TrainingRelation("commerce.labels", LABELS.entity_keys, "event_time", ("event_time",)),
            FEATURES,
        ),
        (
            TrainingRelation(
                "commerce.labels", LABELS.entity_keys, "event_time", ("churned", "churned")
            ),
            FEATURES,
        ),
        (
            LABELS,
            FeatureRelation(
                "commerce.features", 1, LABELS.entity_keys, "available_at", ("churned",)
            ),
        ),
        (
            LABELS,
            FeatureRelation(
                "commerce.features", 1, LABELS.entity_keys, "available_at", ("available_at",)
            ),
        ),
    ],
)
def test_rejects_colliding_output_columns(labels, features):
    with pytest.raises(FeaturePlanError):
        compile_training_set(labels, features)
