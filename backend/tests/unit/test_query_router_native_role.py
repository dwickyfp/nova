from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.modules.query import router as query_router


def test_native_rbac_accepts_session_without_active_role(monkeypatch):
    monkeypatch.setattr(query_router.settings, "RANGER_ENABLED", False)
    assert query_router._resolve_active_role({"username": "analyst", "active_role": None}) is None


def test_native_rbac_accepts_granted_roles_without_active_role(monkeypatch):
    monkeypatch.setattr(query_router.settings, "RANGER_ENABLED", False)
    assert query_router._resolve_active_role(
        {"username": "analyst", "active_role": None, "assigned_roles": ["reader"]}
    ) is None


def test_native_rbac_rejects_ungranted_active_role(monkeypatch):
    monkeypatch.setattr(query_router.settings, "RANGER_ENABLED", False)
    with pytest.raises(HTTPException) as caught:
        query_router._resolve_active_role(
            {
                "username": "analyst",
                "active_role": "ACCOUNTADMIN",
                "assigned_roles": ["reader"],
            }
        )
    assert caught.value.status_code == 403


def test_ranger_mode_still_requires_assigned_active_role(monkeypatch):
    monkeypatch.setattr(query_router.settings, "RANGER_ENABLED", True)
    with pytest.raises(HTTPException) as caught:
        query_router._resolve_active_role(
            {"username": "analyst", "active_role": None, "assigned_roles": ["reader"]}
        )
    assert caught.value.status_code == 403
