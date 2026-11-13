"""The AI model registry must preserve a usable embedding contract."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.common import nova_system
from app.modules.access_control.security_context import SecurityContext
from app.modules.ai_ml import router as ai_router_module
from app.modules.ai_ml import service as service_module
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


ADMIN = SecurityContext("admin", "ACCOUNTADMIN", session_id="s1")


@pytest.fixture
def registry(monkeypatch, fake_metadata_db):
    registry = AsyncMock()
    registry.provider.return_value = {"id": "provider-1", "is_deleted": False}
    registry.models.return_value = []
    registry.model.return_value = {
        "id": "model-1",
        **EMBEDDING,
        "is_deleted": False,
        "metadata_revision": 4,
    }
    monkeypatch.setattr(service_module, "AIRegistryRepository", lambda connection: registry)
    return registry


async def test_alias_collision_is_rejected(registry):
    registry.models.return_value = [{"id": "other-model", **EMBEDDING}]
    with pytest.raises(ValueError, match="alias already exists"):
        await AIService()._assert_unique_alias(registry, "nova.embedding.default")
    registry.models.assert_awaited_once_with()


async def test_existing_alias_can_be_updated(registry):
    registry.models.return_value = [{"id": "model-1", **EMBEDDING}]
    await AIService()._assert_unique_alias(
        registry,
        "nova.embedding.default",
        exclude_id="model-1",
    )


async def test_model_type_is_immutable(registry, fake_metadata_db):
    registry.model.return_value = {
        "id": "model-1",
        "type": "llm",
        "is_deleted": False,
    }
    with pytest.raises(ValueError, match="cannot be changed"):
        await AIService().update_model("model-1", {"type": "embedding"}, actor=ADMIN)
    assert fake_metadata_db.transactions[-1].outcome == "rolled_back"


async def test_create_embedding_persists_revision_and_dimensions(
    registry,
    monkeypatch,
    fake_metadata_db,
):
    changes = AsyncMock()
    changes.create_model.return_value = SimpleNamespace(record={"id": "new", **EMBEDDING})
    monkeypatch.setattr(service_module, "AIConfigurationService", lambda connection: changes)
    result = await AIService().create_model(EMBEDDING, "admin", actor=ADMIN)
    assert result["revision"] == "2026-09"
    definition = changes.create_model.call_args.args[1]
    assert (definition.revision, definition.dimensions) == ("2026-09", 1536)
    assert (definition.logical_alias, definition.modality, definition.metric) == (
        "nova.embedding.default",
        "text",
        "cosine",
    )
    assert len(fake_metadata_db.committed) == 1
    assert fake_metadata_db.committed[0].actor == ADMIN


async def test_create_model_rejects_missing_provider(registry, fake_metadata_db):
    registry.provider.return_value = None
    with pytest.raises(ValueError, match="Provider not found"):
        await AIService().create_model(EMBEDDING, "admin", actor=ADMIN)
    assert fake_metadata_db.transactions[-1].outcome == "rolled_back"


async def test_embedding_revision_and_dimensions_cannot_change(registry, fake_metadata_db):
    for change in ({"revision": "rev-2"}, {"dimensions": 768}, {"name": "other"}):
        with pytest.raises(ValueError, match="immutable"):
            await AIService().update_model("model-1", change, actor=ADMIN)
    assert all(tx.outcome == "rolled_back" for tx in fake_metadata_db.transactions)


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

    async def create(_data, _username, *, actor):
        assert actor == ADMIN
        return {"id": "model-1", **EMBEDDING}

    async def audit(**event):
        events.append(event)

    monkeypatch.setattr(ai_router_module.ai_service, "create_model", create)
    monkeypatch.setattr(ai_router_module, "write_audit_log", audit)
    response = await ai_router_module.create_model(
        "provider-1",
        AIModelCreate.model_validate(EMBEDDING),
        {
            "username": "admin",
            "session_id": "s1",
            "active_role": "ACCOUNTADMIN",
            "assigned_roles": ["ACCOUNTADMIN"],
        },
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
