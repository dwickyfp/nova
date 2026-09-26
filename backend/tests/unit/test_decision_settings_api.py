from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.core.exceptions import register_exception_handlers
from app.modules.ai_ml import router as module
from app.modules.ai_ml.decision_settings import DecisionSettings


@pytest.mark.parametrize("role,expected", [("analyst", 403), ("ACCOUNTADMIN", 200)])
def test_settings_writes_require_admin_and_are_audited(monkeypatch, role, expected):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(module.router, prefix="/ai")
    app.dependency_overrides[get_current_user] = lambda: {
        "username": "admin",
        "active_role": role,
        "roles": [role],
    }
    save = AsyncMock(return_value=DecisionSettings())
    audit = AsyncMock()
    monkeypatch.setattr(module, "save_decision_settings", save)
    monkeypatch.setattr(module, "write_audit_log", audit)
    with TestClient(app) as client:
        response = client.put("/ai/decision-settings", json={"enabled": False})
    assert response.status_code == expected, response.text
    assert save.await_count == (expected == 200)
    assert audit.await_count == (expected == 200)


def test_invalid_enable_cannot_write_settings(monkeypatch):
    app = FastAPI()
    app.include_router(module.router, prefix="/ai")
    app.dependency_overrides[get_current_user] = lambda: {
        "username": "admin",
        "active_role": "ACCOUNTADMIN",
        "roles": ["ACCOUNTADMIN"],
    }
    save = AsyncMock()
    monkeypatch.setattr(module, "save_decision_settings", save)
    with TestClient(app) as client:
        response = client.put("/ai/decision-settings", json={"enabled": True})
    assert response.status_code == 422
    save.assert_not_called()
