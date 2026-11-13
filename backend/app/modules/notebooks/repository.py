"""Durable notebook metadata in PostgreSQL.

* Runtimes: one row per runtime lifecycle (ready, stopped, failed or lost),
  written by the worker that holds the runtime.
* Executions: one row per finished execution, written once by the worker when
  it ends. Queued and running state lives in Redis, so no row has two writers.
* Packages: each person's requested packages for a notebook (their manifest),
  restored into every new runtime.

Every row belongs to one person. Each call runs in an actor-bound metadata
transaction for that person: the request's session for API reads and package
removal, or the runtime's captured identity for the worker. Output payloads
are objects in the Workspace bucket; rows keep their key.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from app.core.metadata_scope import metadata_transaction
from app.modules.access_control.security_context import SecurityContext
from app.modules.notebooks.metadata_repository import NotebookMetadataRepository

if TYPE_CHECKING:
    from app.modules.notebooks.runtime.state import RuntimeState


def runtime_actor(runtime: RuntimeState) -> SecurityContext:
    return notebook_actor(
        runtime.user, runtime.active_role, session_id=runtime.session_id,
        security_version=runtime.security_version,
    )


Packages = list[dict[str, Any]]


def notebook_actor(
    user: str, active_role: str, *, security_version: int = 1, session_id: str = "",
) -> SecurityContext:
    """The person a runtime or job acts for, as captured when it started."""
    return SecurityContext(
        principal=user,
        active_role=active_role,
        session_id=session_id or None,
        security_context_version=max(int(security_version or 1), 1),
    )


def job_actor(job: Mapping[str, Any]) -> SecurityContext:
    return notebook_actor(
        str(job.get("user") or ""), str(job.get("active_role") or ""),
        security_version=int(job.get("security_version") or 1),
        session_id=str(job.get("session_id") or ""),
    )


class NotebookRepository:
    async def ensure_schema(self) -> None:
        """Nothing to create: the PostgreSQL migrations own the schema."""

    async def record_runtime(self, actor: SecurityContext, row: dict[str, Any]) -> None:
        """Create or advance the runtime's lifecycle row (single writer: its worker)."""
        async with metadata_transaction(actor) as scope:
            await NotebookMetadataRepository(scope.connection, actor).record_runtime(row)

    async def record_execution(self, actor: SecurityContext, row: dict[str, Any]) -> None:
        async with metadata_transaction(actor) as scope:
            await NotebookMetadataRepository(scope.connection, actor).record_execution(row)

    async def get_execution(
        self, actor: SecurityContext, notebook_id: str, execution_id: str,
    ) -> dict | None:
        async with metadata_transaction(actor, read_only=True) as scope:
            return await NotebookMetadataRepository(scope.connection, actor).get_execution(
                notebook_id, execution_id
            )

    async def latest_executions(
        self, actor: SecurityContext, notebook_id: str,
    ) -> list[dict[str, Any]]:
        """The newest finished execution of each cell of a notebook, for this person."""
        async with metadata_transaction(actor, read_only=True) as scope:
            return await NotebookMetadataRepository(
                scope.connection, actor
            ).latest_executions(notebook_id)

    async def get_packages(self, actor: SecurityContext, notebook_id: str) -> Packages:
        async with metadata_transaction(actor, read_only=True) as scope:
            packages, _revision = await NotebookMetadataRepository(
                scope.connection, actor
            ).packages(notebook_id)
            return packages

    async def modify_packages(
        self, actor: SecurityContext, notebook_id: str, change: Callable[[Packages], Packages],
    ) -> Packages:
        """Apply ``change`` to this person's manifest under its row lock."""
        async with metadata_transaction(actor) as scope:
            packages, _revision = await NotebookMetadataRepository(
                scope.connection, actor
            ).modify_packages(notebook_id, change)
            return packages


notebook_repository = NotebookRepository()
