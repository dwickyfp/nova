"""Delegated BI sessions for Superset's background work (S5, D4).

Alerts, reports, cache warmup and other work Superset runs without a signed-in
browser execute as the object's owner under the owner's current default role.
They use the same contract as scheduled tasks: the dedicated worker account
(``WORKER_IMPERSONATION_*``) impersonates the owner, which Ranger allows only
for owners it was granted per owner (``scripts/provision_task_worker_access.py``).
Delegation is authorized by actually impersonating the owner and reading the
default role from that session, so the engine, not a Nova lookup, decides.
Nothing falls back to a service identity.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.core.config import settings
from app.modules.access_control.role_activation import RoleActivationError, role_activation_service
from app.modules.task_orchestration.credentials import CredentialUnavailable

_PRINCIPAL = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
_PRIVILEGED = frozenset({"root", "nova_admin"})


class DelegationError(Exception):
    """The owner cannot be delegated to; the message is safe to audit and show."""


def _executor() -> Any:
    from app.modules.task_orchestration.execution import DelegateExecutor

    user = settings.WORKER_IMPERSONATION_USER
    password = settings.WORKER_IMPERSONATION_PASSWORD
    if not user or not password:
        raise DelegationError("No worker account is configured for BI background work")
    try:
        return DelegateExecutor(
            None,
            impersonation_user=user,
            impersonation_password=password,
            impersonation_role=settings.WORKER_IMPERSONATION_ROLE or None,
        )
    except ValueError as exc:
        raise DelegationError(str(exc)) from None


def check_principal(username: str) -> str:
    """Refuse identities delegation never serves, before touching the engine."""
    if not _PRINCIPAL.fullmatch(username or ""):
        raise DelegationError("Not a delegable principal")
    lowered = username.casefold()
    worker = (settings.WORKER_IMPERSONATION_USER or "").casefold()
    if lowered in _PRIVILEGED or (worker and lowered == worker):
        raise DelegationError("Privileged and service accounts are never delegated to")
    return username


@asynccontextmanager
async def _impersonated(username: str) -> AsyncIterator[Any]:
    try:
        async with _executor().owner_connection(username) as connection:
            yield connection
    except CredentialUnavailable as exc:
        raise DelegationError(str(exc)) from None


async def authorize(username: str) -> str:
    """Return ``username``'s default role if the worker may act for them now."""
    check_principal(username)
    async with _impersonated(username) as connection:
        try:
            assignments = await role_activation_service.assignments(connection)
        except RoleActivationError as exc:
            raise DelegationError(str(exc)) from None
    if not assignments.default_role:
        raise DelegationError("The owner has no explicit default role")
    return assignments.default_role


@asynccontextmanager
async def owner_session(username: str, role: str) -> AsyncIterator[Any]:
    """An engine session as ``username`` with exactly ``role`` active."""
    check_principal(username)
    async with _impersonated(username) as connection:
        try:
            active, _assignments = await role_activation_service.activate(
                connection, principal=username, requested_role=role, known_default_role=role
            )
        except RoleActivationError as exc:
            raise DelegationError(str(exc)) from None
        if active != role:
            raise DelegationError("The owner's default role could not be activated")
        yield connection
