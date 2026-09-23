"""Feature Group training passes a pinned PIT plan into the existing ML engine."""

import pytest
from pydantic import ValidationError

from app.core.database import db
from app.modules.intelligence.feature_store import FeatureTrainRequest, feature_store
from app.modules.ml_engine.service import ml_engine_service


def test_training_requires_label_target():
    with pytest.raises(ValidationError):
        FeatureTrainRequest(
            label_relation="NOVA_SYSTEM.labels",
            entity_keys=["customer_id"],
            event_timestamp="label_ts",
            label_columns=["churn"],
            model_name="churn_model",
            model_type="classification",
            target_column="future",
        )


async def test_training_persists_model_and_exact_feature_versions(monkeypatch):
    calls = []

    async def authorized(_name, _user, _version=None):
        return {}, {}, [{"view_name": "behavior", "version": 3}]

    async def version(_name, _version):
        return {"definition": {"feature_columns": ["orders_7d"]}}

    async def training(_name, _body, _user):
        return {
            "id": "training-1",
            "sql": "SELECT churn,orders_7d FROM labels",
            "lineage": {
                "group_version": 2,
                "views": [
                    {"view_name": "behavior", "version": 3},
                ],
            },
        }

    async def train_model(**kwargs):
        calls.append(kwargs)
        return {"model_id": "model-1", "version": 4, "status": "READY"}

    async def execute(sql, parameters=None):
        calls.append({"sql": sql, "parameters": parameters})

    async def audit(*_args):
        return None

    monkeypatch.setattr(feature_store, "_authorized_group", authorized)
    monkeypatch.setattr(feature_store, "_view_version", version)
    monkeypatch.setattr(feature_store, "create_training_set", training)
    monkeypatch.setattr(ml_engine_service, "train_model", train_model)
    monkeypatch.setattr(db, "execute_system", execute)
    monkeypatch.setattr("app.modules.intelligence.feature_store.decrypt_password", lambda _: "pw")
    monkeypatch.setattr("app.modules.intelligence.feature_store._audit", audit)
    result = await feature_store.train(
        "customer",
        FeatureTrainRequest(
            label_relation="NOVA_SYSTEM.labels",
            entity_keys=["customer_id"],
            event_timestamp="label_ts",
            label_columns=["churn"],
            group_version=2,
            model_name="churn_model",
            model_type="classification",
            target_column="churn",
        ),
        {"username": "alice", "encrypted_password": "encrypted"},
    )
    assert calls[0]["training_sql"] == "SELECT churn,orders_7d FROM labels"
    assert calls[0]["feature_columns"] == ["orders_7d"]
    assert calls[1]["parameters"][5] == 2
    assert result["model"]["version"] == 4
