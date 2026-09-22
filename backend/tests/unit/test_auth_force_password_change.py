"""Login/session wiring for the forced first-login password change.

A user created with a generated password is flagged; login returns
``PASSWORD_CHANGE_REQUIRED`` (not a normal session), and changing the password
clears the flag. The user is authenticated — the credential is valid — but the
UI must route them to the change screen before anything else.
"""

from __future__ import annotations

import pytest

from app.modules.auth import service as service_module
from app.modules.auth.service import AuthService


@pytest.fixture
def service(monkeypatch):
    svc = AuthService()

    async def ok_credentials(username, password):
        return True

    async def roles(username, password):
        return ["public"]

    async def security(username, password):
        return {
            "roles": ["public"],
            "assigned_roles": ["public"],
            "default_role": "public",
            "active_role": "public",
            "security_context_version": 1,
        }

    async def no_setup():
        return True

    async def session_create(*args, **kwargs):
        return "sess-1"

    async def noop_audit(**kwargs):
        return None

    monkeypatch.setattr(svc, "verify_credentials", ok_credentials)
    monkeypatch.setattr(svc, "get_user_roles", roles)
    monkeypatch.setattr(svc, "_resolve_security_state", security)
    monkeypatch.setattr(service_module, "is_setup_complete", no_setup)
    monkeypatch.setattr(service_module, "write_audit_log", noop_audit)
    monkeypatch.setattr(service_module.session_store, "create", session_create, raising=False)
    monkeypatch.setattr(service_module, "encrypt_password", lambda _: "enc")
    monkeypatch.setattr(service_module, "create_access_token", lambda u, s: "tok")
    return svc


async def test_login_requires_a_change_when_flagged(service, monkeypatch):
    async def flagged(username):
        return True

    monkeypatch.setattr(service_module, "is_must_change_password", flagged)

    result = await service.login("dwicky", "generated-pass")

    assert result["status"] == "PASSWORD_CHANGE_REQUIRED"
    assert result["access_token"] == "tok"


async def test_login_is_normal_when_not_flagged(service, monkeypatch):
    async def not_flagged(username):
        return False

    monkeypatch.setattr(service_module, "is_must_change_password", not_flagged)

    result = await service.login("dwicky", "real-pass")

    assert result["status"] == "AUTHENTICATED"


async def test_change_password_clears_the_flag(service, monkeypatch):
    cleared: list[str] = []

    async def verify(username, password):
        return True

    async def clear(username):
        cleared.append(username)

    async def execute_system(sql, params=None):
        return {"columns": [], "rows": [], "affected": 1}

    monkeypatch.setattr(service, "verify_credentials", verify)
    monkeypatch.setattr(service_module, "clear_must_change_password", clear)
    monkeypatch.setattr(service_module.db, "execute_system", execute_system)

    result = await service.change_password("dwicky", "old", "NewPass123!", "NewPass123!")

    assert result["status"] == "PASSWORD_CHANGED"
    assert cleared == ["dwicky"]
