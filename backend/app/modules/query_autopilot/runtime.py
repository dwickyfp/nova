"""Delegated Autopilot I/O through the shared query service and task identity owner."""

from __future__ import annotations

from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.redis import session_store
from app.modules.access_control.security_context import SecurityContext
from app.modules.query_autopilot.models import Scope
from app.modules.query_autopilot.telemetry import purpose
from app.modules.task_orchestration.credentials import CredentialUnavailable
from app.modules.task_orchestration.execution import DelegateExecutor


class AuthorizationUnavailable(ValueError):
    pass


class EvidenceUnavailable(ValueError):
    pass


class EvidenceUnsupported(ValueError):
    pass


async def current_policy_revision() -> str | None:
    if not settings.RANGER_ENABLED:
        return None
    from app.integrations.ranger.client import ranger_client

    try:
        return await ranger_client.policy_revision()
    except Exception:
        raise AuthorizationUnavailable("ranger_policy_revision_unavailable") from None


async def validate_policy_revision(scope: Scope) -> None:
    if settings.RANGER_ENABLED and scope.policy_revision != await current_policy_revision():
        raise AuthorizationUnavailable("ranger_policy_revision_changed")


class AuthorizedSQL:
    def __init__(self, executor: DelegateExecutor | None = None) -> None:
        self.executor = executor

    async def capabilities(self):
        from app.sql_frontend.capabilities.starrocks import resolve_engine_capabilities

        return await resolve_engine_capabilities()

    @asynccontextmanager
    async def connection(self, scope: Scope, *, session_id: str | None = None):
        if not scope.active_role:
            raise AuthorizationUnavailable("explicit_active_role_required")
        if scope.catalog != "default_catalog":
            raise EvidenceUnsupported("external_catalog_replay_not_enrolled")
        await validate_policy_revision(scope)
        if session_id:
            session = await session_store.get(session_id)
            if not session:
                raise AuthorizationUnavailable("session_expired")
            context = SecurityContext.from_session(session, database=scope.database)
            if (context.principal, context.active_role, context.security_context_version) != (
                scope.principal,
                scope.active_role,
                scope.security_context_version,
            ):
                raise AuthorizationUnavailable("security_context_changed")
            from app.core.database import db
            from app.core.security import decrypt_password

            async with db.user_conn(
                scope.principal, decrypt_password(session["encrypted_password"])
            ) as conn:
                yield conn
        elif self.executor is not None:
            try:
                async with self.executor.owner_connection(scope.principal) as conn:
                    yield conn
            except CredentialUnavailable:
                raise AuthorizationUnavailable("delegated_credential_unavailable") from None
        else:
            raise AuthorizationUnavailable("delegated_identity_unavailable")

    async def execute(
        self,
        sql: str,
        scope: Scope,
        *,
        connection,
        category: str,
        max_rows: int = 2000,
        confirm: bool = False,
    ):
        from app.modules.query.service import query_service

        await validate_policy_revision(scope)
        try:
            with purpose(category):
                result = await query_service.execute(
                    sql=sql,
                    username=scope.principal,
                    encrypted_password="",
                    database=scope.database,
                    role=scope.active_role,
                    security_context_version=scope.security_context_version,
                    connection=connection,
                    max_rows=max_rows,
                    confirm_destructive=confirm,
                )
        except Exception as exc:
            cause = exc.__cause__ or exc
            code = cause.args[0] if cause.args and isinstance(cause.args[0], int) else None
            if getattr(exc, "status_code", None) in {401, 403} or code in {
                1044,
                1045,
                1142,
                1143,
                5203,
                5204,
            }:
                raise AuthorizationUnavailable("engine_authorization_refused") from None
            raise EvidenceUnavailable("engine_operation_unavailable") from None
        if result.needs_confirmation:
            raise AuthorizationUnavailable("explicit_execution_consent_required")
        if result.error:
            raise EvidenceUnavailable("engine_operation_unavailable")
        return result


def worker_sql() -> AuthorizedSQL:
    if not settings.WORKER_IMPERSONATION_USER or not settings.WORKER_IMPERSONATION_PASSWORD:
        return AuthorizedSQL()
    return AuthorizedSQL(
        DelegateExecutor(
            None,
            impersonation_user=settings.WORKER_IMPERSONATION_USER,
            impersonation_password=settings.WORKER_IMPERSONATION_PASSWORD,
            impersonation_role=settings.WORKER_IMPERSONATION_ROLE,
        )
    )
