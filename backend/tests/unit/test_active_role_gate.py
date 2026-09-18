"""Regression tests for the active-role gate — NOVA-94 security finding #3.

``require_role`` checks membership in the roles a user has been *granted*, but
the engine connection activates ``user["active_role"]``. A principal with
``ACCOUNTADMIN`` granted but ``analyst`` active passed the granted-roles gate
and then executed as ``analyst``, so for a destructive surface the backend gate
was weaker than documented.

``require_active_role`` gates on the role the engine will actually run under.
These tests exercise the dependency directly, so no engine or HTTP client is
needed.
"""

import pytest

from app.core.deps import require_role
from app.core.exceptions import InsufficientRoleError
from app.core.role_gates import require_active_role

BACKUP_ROLES = ("ACCOUNTADMIN", "cluster_admin", "db_admin")


async def _run(factory, allowed_roles, user):
    check = factory(*allowed_roles)
    return await check(user=user)


class TestActiveRoleGate:
    async def test_active_admin_is_allowed(self):
        user = {
            "username": "admin",
            "roles": ["ACCOUNTADMIN"],
            "active_role": "ACCOUNTADMIN",
        }
        assert await _run(require_active_role, BACKUP_ROLES, user) == user

    async def test_granted_admin_with_switched_active_role_is_denied(self):
        """The reported bypass: granted ACCOUNTADMIN, active analyst."""
        user = {
            "username": "switcher",
            "roles": ["ACCOUNTADMIN", "analyst"],
            "active_role": "analyst",
        }
        with pytest.raises(InsufficientRoleError) as exc:
            await _run(require_active_role, BACKUP_ROLES, user)
        assert exc.value.status_code == 403

    async def test_non_granted_principal_is_denied(self):
        user = {"username": "analyst", "roles": ["analyst"], "active_role": "analyst"}
        with pytest.raises(InsufficientRoleError):
            await _run(require_active_role, BACKUP_ROLES, user)

    async def test_missing_active_role_falls_back_to_grants(self):
        """A session that never switched keeps working on its granted role."""
        user = {"username": "admin", "roles": ["ACCOUNTADMIN"], "active_role": None}
        assert await _run(require_active_role, BACKUP_ROLES, user) == user

    async def test_roleless_session_with_no_active_role_is_denied(self):
        user = {"username": "nobody", "roles": [], "active_role": None}
        with pytest.raises(InsufficientRoleError):
            await _run(require_active_role, BACKUP_ROLES, user)


class TestGrantedRoleGateIsUnchanged:
    """The existing semantics must not move for callers that rely on them.

    The user-administration module gates on *granted* roles deliberately (it
    talks to StarRocks as ``root``, not as the caller) and documents that
    behaviour; ``require_role`` keeps it.
    """

    async def test_granted_admin_with_switched_active_role_still_passes(self):
        user = {
            "username": "switcher",
            "roles": ["ACCOUNTADMIN", "analyst"],
            "active_role": "analyst",
        }
        assert await _run(require_role, BACKUP_ROLES, user) == user

    async def test_non_granted_principal_is_still_denied(self):
        user = {"username": "analyst", "roles": ["analyst"], "active_role": "analyst"}
        with pytest.raises(InsufficientRoleError):
            await _run(require_role, BACKUP_ROLES, user)
