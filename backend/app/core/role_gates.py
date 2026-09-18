"""Authorization gates that depend on which role the engine will activate.

NOVA-94 security finding #3. ``app.core.deps.require_role`` checks membership
in the roles a user has been **granted** (``user["roles"]``). Every engine
statement, however, runs as ``user["active_role"]``: the connection issues
``SET ROLE <active_role>``, and ``auth`` lets a user switch that role. A
principal who holds ``ACCOUNTADMIN`` but has switched to ``analyst`` therefore
passed a granted-roles gate and then executed as ``analyst`` — so on a
destructive surface the backend gate was weaker than its docstring claimed.

``require_active_role`` gates on the role the engine will actually run under,
which is what makes the backend check authoritative. It lives here rather than
in ``app.core.deps`` so that adding it does not drag that module's pre-existing
ruff baseline into a changed-files lint diff, and so the granted-vs-active
distinction has one obvious home.

``require_role`` keeps its granted-roles semantics deliberately: the
user-administration module gates on grants because it talks to StarRocks as
``root`` rather than as the caller, and documents that behaviour.
"""

from typing import Annotated

from fastapi import Depends

from app.core.deps import get_current_user
from app.core.exceptions import InsufficientRoleError


def require_active_role(*allowed_roles: str):
    """Dependency factory: require the user's *active* role to be one of these.

    Usage:
        @router.post("/backup")
        async def backup(user=Depends(require_active_role("ACCOUNTADMIN"))):
            ...

    A session with no ``active_role`` at all falls back to the granted set: such
    a session authenticates as its default role, and refusing it outright would
    break logins that never switched roles explicitly.
    """

    async def _check(user: Annotated[dict, Depends(get_current_user)]) -> dict:
        active_role = user.get("active_role")
        if active_role is None:
            if not any(r in user["roles"] for r in allowed_roles):
                raise InsufficientRoleError(f"Requires one of: {', '.join(allowed_roles)}")
            return user
        if active_role not in allowed_roles:
            raise InsufficientRoleError(
                f"Requires active role to be one of: {', '.join(allowed_roles)} "
                f"(currently {active_role})"
            )
        return user

    return _check
