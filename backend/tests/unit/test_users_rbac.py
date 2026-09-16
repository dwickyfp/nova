"""Regression tests for server-side RBAC on the user/role admin surface.

Acceptance (e): a non-admin user must be *denied* on ``/api/v1/users`` — the
denied path, not just the allowed one. The service layer reaches StarRocks over
``_root_connect()``, so without this guard every authenticated user could drop
roles and reset passwords.

These are unit-level: the dependency chain is exercised directly, so no engine
and no HTTP client are needed.
"""

import pytest

from app.core.deps import require_role
from app.core.exceptions import InsufficientRoleError
from app.modules.users.router import ADMIN_ROLES, router

ADMIN_USER = {"username": "admin", "roles": ["ACCOUNTADMIN"]}
ANALYST_USER = {"username": "analyst", "roles": ["test_analyst"]}
ROLELESS_USER = {"username": "nobody", "roles": []}


async def _run_guard(allowed_roles, user):
    """Invoke the require_role dependency's inner check with a fake user."""
    check = require_role(*allowed_roles)

    async def fake_get_current_user():
        return user

    # The dependency factory closes over get_current_user; call the body directly
    # with the user it would have received.
    return await check(user=user)


class TestRequireRoleDependency:
    async def test_admin_is_allowed(self):
        assert await _run_guard(ADMIN_ROLES, ADMIN_USER) == ADMIN_USER

    async def test_non_admin_is_denied(self):
        with pytest.raises(InsufficientRoleError):
            await _run_guard(ADMIN_ROLES, ANALYST_USER)

    async def test_roleless_user_is_denied(self):
        with pytest.raises(InsufficientRoleError):
            await _run_guard(ADMIN_ROLES, ROLELESS_USER)

    async def test_denial_is_403(self):
        with pytest.raises(InsufficientRoleError) as exc:
            await _run_guard(ADMIN_ROLES, ANALYST_USER)
        assert exc.value.status_code == 403


class TestEveryUsersEndpointIsGuarded:
    """Every route on the users router must depend on require_role."""

    def _routes(self):
        return [r for r in router.routes if hasattr(r, "dependant")]

    def test_router_is_not_empty(self):
        assert self._routes()

    @pytest.mark.parametrize(
        "route",
        [r for r in router.routes if hasattr(r, "dependant")],
        ids=lambda r: f"{sorted(r.methods)[0] if r.methods else '?'} {r.path}",
    )
    def test_route_uses_require_role(self, route):
        """A route guarded only by get_current_user would be a privilege hole."""
        dep_names = _dependency_names(route.dependant)
        assert "require_role.<locals>._check" in dep_names, (
            f"{route.path} is not guarded by require_role — only {dep_names}"
        )

    @pytest.mark.parametrize(
        "route",
        [r for r in router.routes if hasattr(r, "dependant")],
        ids=lambda r: f"{sorted(r.methods)[0] if r.methods else '?'} {r.path}",
    )
    def test_route_does_not_use_bare_get_current_user(self, route):
        dep_names = _dependency_names(route.dependant)
        assert "get_current_user" not in dep_names or "require_role.<locals>._check" in dep_names


def _dependency_names(dependant) -> set[str]:
    """Collect the qualified names of a route's dependency callables."""
    names: set[str] = set()
    stack = [dependant]
    while stack:
        node = stack.pop()
        call = getattr(node, "call", None)
        if call is not None:
            names.add(f"{getattr(call, '__qualname__', getattr(call, '__name__', ''))}")
        stack.extend(getattr(node, "dependencies", []) or [])
    return names


class TestAdminRoleSet:
    def test_accountadmin_included(self):
        assert "ACCOUNTADMIN" in ADMIN_ROLES

    def test_plain_analyst_role_not_included(self):
        assert "test_analyst" not in ADMIN_ROLES

    def test_public_role_not_admin(self):
        assert "public" not in ADMIN_ROLES
