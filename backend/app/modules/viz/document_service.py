"""Session admission and document commands shared by HTTP and assistant callers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from app.common.audit import write_audit_log
from app.core.exceptions import NovaException, SessionExpiredError
from app.core.metadata_db import MetadataPermissionDenied
from app.core.metadata_scope import actor_for, metadata_transaction
from app.core.redis import session_store
from app.modules.access_control.security_context import SecurityContext, SecurityContextError
from app.modules.users.service import user_service
from app.modules.workspaces.spaces import Space, space_service

from .contracts.charts import ChartDefinition
from .contracts.commands import (
    DeleteRequest,
    GrantsReplace,
    Operation,
    PublicationRequest,
    RestoreRequest,
)
from .contracts.dashboards import DashboardDefinition
from .contracts.datasets import DatasetDefinition
from .contracts.http import dataset_publication
from .documents import Kind, VizDocumentRepository, document_identity


def _scheduled_session(expected: SecurityContext) -> dict[str, Any] | None:
    """A scheduled run inside the task worker, which has no sign-in session.

    The worker opens a ``task:`` session only after the engine impersonated
    exactly this account with exactly this role active, and marks the
    connection as delegated to it. Anything else still needs a live session.
    """
    from app.modules.query.service import delegated_username

    if not (expected.session_id or "").startswith("task:"):
        return None
    if delegated_username() != expected.principal:
        return None
    return {
        "username": expected.principal, "session_id": expected.session_id,
        "active_role": expected.active_role, "assigned_roles": [expected.active_role],
        "security_context_version": expected.security_context_version,
    }


async def current_session(user: Mapping[str, Any]) -> dict[str, Any]:
    """Revalidate the original caller; a role switch requires a new command."""
    expected = actor_for(user)
    if not expected.session_id:
        raise SessionExpiredError()
    scheduled = _scheduled_session(expected)
    if scheduled is not None:
        return scheduled
    stored = await session_store.get(expected.session_id)
    if not stored:
        raise SessionExpiredError()
    version = stored.get("security_context_version")
    if type(version) is not int or version < 1:
        raise MetadataPermissionDenied()
    assigned = stored.get("assigned_roles")
    if (not isinstance(assigned, list) or not assigned
            or any(not isinstance(role, str) or not role or role != role.strip()
                   for role in assigned)):
        raise MetadataPermissionDenied()
    session = {
        "username": stored.get("username"), "session_id": expected.session_id,
        "active_role": stored.get("active_role"),
        "assigned_roles": assigned,
        "security_context_version": version,
    }
    try:
        current = SecurityContext.from_session(session)
    except (SecurityContextError, TypeError, ValueError):
        raise MetadataPermissionDenied() from None
    if current != expected:
        raise MetadataPermissionDenied()
    return session


@asynccontextmanager
async def document_admission(
    user: Mapping[str, Any], *, action: str, identity: UUID | None = None,
) -> AsyncIterator[SecurityContext]:
    """Audit refusals before workspace resolution as well as inside document commands."""
    actor = actor_for(user)
    try:
        yield actor
    except NovaException as error:
        if error.status_code in {401, 403, 404}:
            await write_audit_log(
                event_type="viz", action=action, object_type="viz_resource",
                object_name=str(identity) if identity else "document_collection",
                user_name=actor.principal, active_role=actor.active_role,
                session_id=actor.session_id,
                security_context_version=actor.security_context_version,
                status="DENIED", decision="DENY",
            )
        raise
    except ValueError:
        # ValidationError includes input values. Do not echo persisted SQL,
        # parameter values or private definitions through the command boundary.
        raise NovaException("Invalid document request", status_code=422) from None


@asynccontextmanager
async def document_transaction(
    user: Mapping[str, Any], *, action: str, identity: UUID | None = None,
    read_only: bool = False,
) -> AsyncIterator[VizDocumentRepository]:
    """Short actor-bound metadata work; no query, directory I/O or retry inside."""
    async with document_admission(user, action=action, identity=identity) as actor:
        session = await current_session(user)
        async with (
            metadata_transaction(actor, read_only=read_only) as scope,
            VizDocumentRepository.bind(scope.connection, session) as repository,
        ):
            yield repository


async def document_workspace(
    user: Mapping[str, Any], workspace_id: str, *, action: str,
    identity: UUID | None = None, edit: bool = False,
) -> Space:
    async with document_admission(user, action=action, identity=identity):
        session = await current_session(user)
        return await space_service.resolve(session, workspace_id, edit=edit)


class NativeDocumentService:
    async def require_kind(self, user: Mapping[str, Any], identity: UUID, kind: Kind) -> None:
        async with document_transaction(
            user, action="READ", identity=identity, read_only=True,
        ) as repository:
            await repository.require_kind(identity, kind)

    async def workspace_of(self, user: Mapping[str, Any], identity: UUID) -> str:
        """The workspace holding a document the caller can see.

        Charts and datasets a dashboard references must live in its workspace,
        so an assistant creating one for a dashboard needs it.
        """
        async with document_transaction(
            user, action="READ", identity=identity, read_only=True,
        ) as repository:
            return str((await repository.head(identity))["workspace_id"])

    async def browse(
        self, user: Mapping[str, Any], workspace_id: str, *, kind: Kind = "dashboard",
        draft: bool = False, before: tuple[datetime, UUID] | None = None, limit: int = 50,
    ) -> list[dict[str, Any]]:
        space = await document_workspace(user, workspace_id, action="LIST")
        async with document_transaction(user, action="LIST", read_only=True) as repository:
            return await repository.browse(space.id, kind=kind, draft=draft,
                                           before=before, limit=limit)

    async def read(
        self, user: Mapping[str, Any], identity: UUID, *, draft: bool = False,
        revision: int | None = None,
    ) -> dict[str, Any]:
        async with document_transaction(
            user, action="READ", identity=identity, read_only=True,
        ) as repository:
            snapshot = await repository.read(identity, draft=draft, revision=revision)
            if not draft and isinstance(snapshot["definition"], DatasetDefinition):
                return {**snapshot, "definition": dataset_publication(snapshot["definition"])}
            return snapshot

    async def dashboard_datasets(
        self, user: Mapping[str, Any], identity: UUID, revision: int,
    ) -> dict[str, Any]:
        async with document_transaction(
            user, action="READ_DATASETS", identity=identity, read_only=True,
        ) as repository:
            return await repository.dashboard_datasets(identity, revision)

    async def create_visual(
        self, user: Mapping[str, Any], workspace_id: str,
        document: ChartDefinition | DashboardDefinition, *, operation_id: UUID,
    ) -> dict[str, Any]:
        """Visuals reference validated dataset revisions; dataset authoring owns sources."""
        if not isinstance(document, (ChartDefinition, DashboardDefinition)):
            raise NovaException("Expected a chart or dashboard definition", status_code=422)
        _, identity = document_identity(document)
        space = await document_workspace(user, workspace_id, action="CREATE", identity=identity,
                                         edit=True)
        async with document_transaction(user, action="CREATE", identity=identity) as repository:
            return await repository.create(space.id, document, operation_id=operation_id)

    async def replace_visual(
        self, user: Mapping[str, Any], document: ChartDefinition | DashboardDefinition,
        *, operation_id: UUID, expected_revision: int,
    ) -> dict[str, Any]:
        if not isinstance(document, (ChartDefinition, DashboardDefinition)):
            raise NovaException("Expected a chart or dashboard definition", status_code=422)
        _, identity = document_identity(document)
        async with document_transaction(user, action="EDIT", identity=identity) as repository:
            return await repository.replace(document, operation_id=operation_id,
                                            expected_revision=expected_revision)

    async def apply(
        self, user: Mapping[str, Any], identity: UUID, operation: Operation,
    ) -> dict[str, Any]:
        async with document_transaction(user, action="EDIT", identity=identity) as repository:
            return await repository.apply(identity, operation)

    async def publish(
        self, user: Mapping[str, Any], identity: UUID, request: PublicationRequest,
    ) -> dict[str, Any]:
        async with document_transaction(user, action="PUBLISH", identity=identity) as repository:
            return await repository.publish(identity, request)

    async def restore(
        self, user: Mapping[str, Any], identity: UUID, request: RestoreRequest,
    ) -> dict[str, Any]:
        async with document_transaction(user, action="RESTORE", identity=identity) as repository:
            return await repository.restore(identity, request)

    async def permissions(self, user: Mapping[str, Any], identity: UUID) -> list[str]:
        async with document_transaction(
            user, action="READ_PERMISSIONS", identity=identity, read_only=True,
        ) as repository:
            return await repository.permissions(identity)

    async def comments(self, user: Mapping[str, Any], identity: UUID) -> list[dict[str, Any]]:
        async with document_transaction(
            user, action="READ_COMMENTS", identity=identity, read_only=True,
        ) as repository:
            return await repository.comments(identity)

    async def add_comment(self, user: Mapping[str, Any], identity: UUID, comment_id: UUID,
                          body: str, *, page_id: UUID | None,
                          widget_id: UUID | None) -> dict[str, Any]:
        async with document_transaction(user, action="COMMENT", identity=identity) as repository:
            return await repository.add_comment(identity, comment_id, body,
                                                page_id=page_id, widget_id=widget_id)

    async def resolve_comment(self, user: Mapping[str, Any], identity: UUID, comment_id: UUID,
                              resolved: bool) -> dict[str, Any]:
        async with document_transaction(
            user, action="RESOLVE_COMMENT", identity=identity,
        ) as repository:
            return await repository.resolve_comment(identity, comment_id, resolved)

    async def delete_comment(self, user: Mapping[str, Any], identity: UUID,
                             comment_id: UUID) -> None:
        async with document_transaction(
            user, action="DELETE_COMMENT", identity=identity,
        ) as repository:
            await repository.delete_comment(identity, comment_id)

    async def scheduled_permissions(self, account: str, role: str, identity: UUID) -> set[str]:
        """What a scheduled run as ``account`` under ``role`` may do with a document.

        A read-only check before a schedule is saved, evaluated by the same
        policy the run will meet; it reads no definition and no data.
        """
        session = {"username": account, "session_id": "schedule-check", "active_role": role,
                   "assigned_roles": [role], "security_context_version": 1}
        try:
            actor = SecurityContext.from_session(session)
            async with (
                metadata_transaction(actor, read_only=True) as scope,
                VizDocumentRepository.bind(scope.connection, session) as repository,
            ):
                return set(await repository.permissions(identity))
        except (NovaException, MetadataPermissionDenied, SecurityContextError):
            return set()

    async def authorize_export(
        self, user: Mapping[str, Any], identity: UUID, *, file_format: str,
        part: UUID | None = None,
    ) -> dict[str, Any]:
        """Allow a download only with the Export permission, and audit either way.

        The file is built from results the caller already reads under their own
        role; this check is the product permission and the audit record.
        """
        permitted = "export" in await self.permissions(user, identity)
        actor = actor_for(user)
        await write_audit_log(
            event_type="viz", action=f"EXPORT_{file_format.upper()}",
            object_type="viz_resource",
            object_name=f"{identity}:{part}" if part else str(identity),
            user_name=actor.principal, active_role=actor.active_role,
            session_id=actor.session_id,
            security_context_version=actor.security_context_version,
            status="SUCCESS" if permitted else "DENIED",
            decision="ALLOW" if permitted else "DENY",
        )
        if not permitted:
            raise NovaException(
                "Your access to this dashboard does not include downloading it.",
                status_code=403,
            )
        return {"allowed": True, "format": file_format}

    async def grants(self, user: Mapping[str, Any], identity: UUID) -> list[dict[str, Any]]:
        async with document_transaction(
            user, action="READ_GRANTS", identity=identity, read_only=True,
        ) as repository:
            return await repository.grants(identity)

    async def grant_state(self, user: Mapping[str, Any], identity: UUID) -> dict[str, Any]:
        async with document_transaction(
            user, action="READ_GRANTS", identity=identity,
        ) as repository:
            return await repository.grant_state(identity)

    async def replace_grants(
        self, user: Mapping[str, Any], identity: UUID, request: GrantsReplace,
    ) -> dict[str, Any]:
        # Check management before directory lookup, and again in the committing
        # transaction. Revocation during the lookup cannot authorize a later write.
        await self.grants(user, identity)
        subjects = {(grant.subject_type, grant.subject_id) for grant in request.grants}
        known: set[tuple[str, str]] = set()
        if any(kind == "user" for kind, _ in subjects):
            known.update(("user", item["username"]) for item in await user_service.list_users())
        if any(kind == "role" for kind, _ in subjects):
            known.update(("role", item["name"]) for item in await user_service.list_roles())
        if not subjects <= known:
            raise NovaException("A sharing recipient is unavailable", status_code=422)
        async with document_transaction(user, action="GRANTS", identity=identity) as repository:
            return await repository.replace_grants(identity, request)

    async def history(
        self, user: Mapping[str, Any], identity: UUID, *,
        before: int | None = None, limit: int = 50,
    ) -> list[dict[str, Any]]:
        async with document_transaction(
            user, action="HISTORY", identity=identity, read_only=True,
        ) as repository:
            return await repository.history(identity, before=before, limit=limit)

    async def receipt(
        self, user: Mapping[str, Any], identity: UUID | None, operation_id: UUID,
    ) -> dict[str, Any]:
        async with document_transaction(
            user, action="RECEIPT", identity=identity, read_only=True,
        ) as repository:
            return await repository.receipt(identity, operation_id)

    async def delete(
        self, user: Mapping[str, Any], identity: UUID, kind: Kind, request: DeleteRequest,
    ) -> dict[str, Any]:
        async with document_transaction(user, action="DELETE", identity=identity) as repository:
            return await repository.delete(identity, kind, request)

    async def events(
        self, user: Mapping[str, Any], identity: UUID, *, after: int = 0, limit: int = 100,
    ) -> dict[str, Any]:
        async with document_transaction(
            user, action="EVENTS", identity=identity, read_only=True,
        ) as repository:
            return await repository.events(identity, after=after, limit=limit)


native_document_service = NativeDocumentService()
