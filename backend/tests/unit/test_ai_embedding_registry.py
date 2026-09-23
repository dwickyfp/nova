"""The AI model registry must preserve a usable embedding contract."""

import pytest
from pydantic import ValidationError

from app.common import nova_system
from app.modules.ai_ml import router as ai_router_module
from app.modules.ai_ml.router import require_admin, router
from app.modules.ai_ml.schemas import AIModelCreate
from app.modules.ai_ml.service import AIService

EMBEDDING = {
    "provider_id": "provider-1",
    "name": "text-embedding-3-small",
    "type": "embedding",
    "logical_alias": "nova.embedding.default",
    "revision": "2026-09",
    "dimensions": 1536,
    "modality": "text",
    "metric": "cosine",
}


@pytest.mark.parametrize("missing", ["logical_alias", "revision", "dimensions"])
def test_embedding_contract_requires_pinned_metadata(missing):
    with pytest.raises(ValidationError):
        AIModelCreate.model_validate(
            {key: value for key, value in EMBEDDING.items() if key != missing}
        )


@pytest.mark.parametrize("dimensions", [0, -1])
def test_embedding_dimensions_must_be_positive(dimensions):
    with pytest.raises(ValidationError):
        AIModelCreate.model_validate({**EMBEDDING, "dimensions": dimensions})


def test_llm_cannot_carry_embedding_metadata():
    with pytest.raises(ValidationError):
        AIModelCreate.model_validate({**EMBEDDING, "type": "llm"})


def test_valid_models():
    assert AIModelCreate.model_validate(EMBEDDING).type == "embedding"
    assert (
        AIModelCreate.model_validate(
            {"provider_id": "provider-1", "name": "gpt", "type": "llm", "max_tokens": 4096}
        ).type
        == "llm"
    )


class FakeCursor:
    def __init__(self, row=None):
        self.row = row
        self.executed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def execute(self, sql, params):
        self.executed.append((sql, params))

    async def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, *_):
        return self._cursor

    def close(self):
        return None


async def test_alias_collision_is_rejected(monkeypatch):
    service = AIService()
    cursor = FakeCursor({"id": "other-model"})

    async def connect():
        return FakeConnection(cursor)

    monkeypatch.setattr(service, "_connect", connect)
    with pytest.raises(ValueError, match="alias already exists"):
        await service._assert_unique_alias("nova.embedding.default")
    assert cursor.executed[0][1] == ("nova.embedding.default",)


async def test_existing_alias_can_be_updated(monkeypatch):
    service = AIService()
    cursor = FakeCursor({"id": "model-1"})

    async def connect():
        return FakeConnection(cursor)

    monkeypatch.setattr(service, "_connect", connect)
    await service._assert_unique_alias("nova.embedding.default", exclude_id="model-1")


async def test_model_type_is_immutable(monkeypatch):
    service = AIService()

    async def get_model(_model_id):
        return {"id": "model-1", "type": "llm"}

    monkeypatch.setattr(service, "get_model", get_model)
    with pytest.raises(ValueError, match="cannot be changed"):
        await service.update_model("model-1", {"type": "embedding"})


async def test_create_embedding_persists_revision_and_dimensions(monkeypatch):
    service = AIService()
    cursor = FakeCursor()

    async def connect():
        return FakeConnection(cursor)

    async def get_provider(_provider_id):
        return {"id": "provider-1"}

    async def get_model(model_id):
        return {"id": model_id, **EMBEDDING}

    async def unique(_alias):
        return None

    monkeypatch.setattr(service, "_connect", connect)
    monkeypatch.setattr(service, "get_provider", get_provider)
    monkeypatch.setattr(service, "get_model", get_model)
    monkeypatch.setattr(service, "_assert_unique_alias", unique)
    result = await service.create_model(EMBEDDING, "admin")
    assert result["revision"] == "2026-09"
    sql, params = cursor.executed[0]
    assert "logical_alias, revision, dimensions" in sql
    assert params[7:9] == ("2026-09", 1536)


async def test_create_model_rejects_missing_provider(monkeypatch):
    service = AIService()

    async def get_provider(_provider_id):
        return None

    monkeypatch.setattr(service, "get_provider", get_provider)
    with pytest.raises(ValueError, match="Provider not found"):
        await service.create_model(EMBEDDING, "admin")


async def test_embedding_revision_and_dimensions_cannot_change(monkeypatch):
    service = AIService()

    async def get_model(_model_id):
        return {"id": "model-1", **EMBEDDING}

    monkeypatch.setattr(service, "get_model", get_model)
    for change in ({"revision": "rev-2"}, {"dimensions": 768}, {"name": "other"}):
        with pytest.raises(ValueError, match="immutable"):
            await service.update_model("model-1", change)


async def test_additive_model_migration_is_idempotent(monkeypatch):
    applied = []

    async def exists(_table, column):
        return column == "revision"

    async def execute(sql):
        applied.append(sql)

    monkeypatch.setattr(nova_system, "_column_exists", exists)
    monkeypatch.setattr(nova_system.db, "execute_system", execute)
    await nova_system.migrate_ai_model_columns()
    assert len(applied) == len(nova_system.AI_MODEL_COLUMN_MIGRATIONS) - 1
    assert all("ADD COLUMN revision" not in sql for sql in applied)


def test_model_writes_require_admin_dependency():
    for route in router.routes:
        if route.path not in {"/providers/{provider_id}/models", "/models/{model_id}"}:
            continue
        if not route.methods.intersection({"POST", "PUT", "DELETE"}):
            continue
        dependencies = [dependency.call for dependency in route.dependant.dependencies]
        assert require_admin.dependency in dependencies


async def test_model_create_writes_audit_without_credential_data(monkeypatch):
    events = []

    async def create(_data, _username):
        return {"id": "model-1", **EMBEDDING}

    async def audit(**event):
        events.append(event)

    monkeypatch.setattr(ai_router_module.ai_service, "create_model", create)
    monkeypatch.setattr(ai_router_module, "write_audit_log", audit)
    response = await ai_router_module.create_model(
        "provider-1",
        AIModelCreate.model_validate(EMBEDDING),
        {"username": "admin", "session_id": "s1", "active_role": "ACCOUNTADMIN"},
    )
    assert response.id == "model-1"
    assert events == [
        {
            "event_type": "AI_MODEL",
            "user_name": "admin",
            "action": "CREATE",
            "object_type": "AI_MODEL",
            "object_name": "model-1",
            "status": "SUCCESS",
            "session_id": "s1",
            "active_role": "ACCOUNTADMIN",
        }
    ]
