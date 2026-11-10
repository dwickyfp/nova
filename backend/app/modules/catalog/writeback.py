"""Governed writes to Postgres and Snowflake catalogs (notebook write-back).

StarRocks reads these sources through JDBC catalogs but cannot write to them
(4.1.4: INSERT/UPDATE/DELETE fail to open a transaction), so a notebook's
``con.write`` / ``con.merge_into`` into such a catalog lands here. The rules:

* **Ranger first.** The caller's active role must be allowed the write on the
  exact ``catalog.schema.table`` (INSERT to append; INSERT and UPDATE to merge
  with updates; DELETE to overwrite or delete matches), checked before any
  credential is read. Deny items win.
* **A separate write credential.** The engine's catalog keeps its read
  credential. A security admin sets a write credential on the connection;
  without one, writes are refused.
* **One transaction, values bound.** The source subclass loads the rows into a
  session table and applies the change in one transaction
  (:meth:`CatalogSource.write_rows`).
* **Audited, never leaking.** Every write and refusal is audited with its row
  counts; driver errors are logged and reach the caller only as a short,
  credential-free message.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.common.audit import write_audit_log
from app.common.sql_guard import CredentialsRedactionError, redact_sql_credentials
from app.core.config import settings
from app.modules.access_control.domain import AccessRequest

from .connections import CONNECTION_PROPERTY, connection_repo, connection_service, endpoint_of
from .errors import CatalogError
from .repository import catalog_repo
from .sources import SOURCES
from .sources.writes import WriteRefused, WriteRequest, WriteResult

logger = logging.getLogger(__name__)


class WritebackDenied(CatalogError):
    """The caller's role may not make this write."""


@dataclass(frozen=True)
class WriteTarget:
    catalog: str
    schema: str
    table: str

    @classmethod
    def parse(cls, name: str) -> WriteTarget:
        parts = [part.strip().strip('`"') for part in name.split(".")]
        if len(parts) != 3 or not all(parts):
            raise WriteRefused("Name the table as catalog.schema.table")
        return cls(*parts)

    def __str__(self) -> str:
        return f"{self.catalog}.{self.schema}.{self.table}"


def required_access(mode: str, when_matched: str, when_not_matched: str) -> tuple[str, ...]:
    if mode == "append":
        return ("insert",)
    if mode == "overwrite":
        return ("delete", "insert")
    needed: list[str] = []
    if when_not_matched == "insert":
        needed.append("insert")
    if when_matched == "update":
        needed.append("update")
    elif when_matched == "delete":
        needed.append("delete")
    return tuple(needed) or ("select",)


async def write(
    *,
    username: str,
    active_role: str,
    target: str,
    columns: tuple[str, ...],
    rows: list[tuple[Any, ...]],
    mode: str = "append",
    keys: tuple[str, ...] = (),
    when_matched: str = "update",
    when_not_matched: str = "insert",
    session_id: str | None = None,
) -> WriteResult:
    """Write rows to an external catalog table as ``username`` under ``active_role``."""
    parsed = WriteTarget.parse(target)
    request = WriteRequest(
        schema=parsed.schema,
        table=parsed.table,
        columns=columns,
        rows=rows,
        mode=mode,
        keys=keys,
        when_matched=when_matched,
        when_not_matched=when_not_matched,
        timeout_seconds=settings.NOTEBOOK_CELL_TIMEOUT_SECONDS,
    )
    audit = _Audit(username, active_role, str(parsed), mode, session_id)
    secrets: tuple[str, ...] = ()
    try:
        if len(rows) > settings.NOTEBOOK_WRITE_MAX_ROWS:
            raise WriteRefused(
                f"One write takes at most {settings.NOTEBOOK_WRITE_MAX_ROWS:,} rows"
            )
        catalog = await catalog_repo.get_catalog(parsed.catalog)
        if catalog is None or catalog.get("dropped_at") is not None:
            raise WriteRefused(f"Catalog {parsed.catalog} does not exist")
        connection_name = (catalog.get("properties") or {}).get(CONNECTION_PROPERTY)
        if catalog.get("kind") != "foreign" or not connection_name:
            raise WriteRefused(
                f"{parsed.catalog} is not an external catalog Nova manages; write Nova "
                "tables through the nova catalog"
            )
        connection = await connection_repo.get(connection_name)
        source = SOURCES.get(connection["connector_type"]) if connection else None
        if connection is None or source is None or not source.supports_writeback:
            raise WriteRefused(f"Nova cannot write to {parsed.catalog}")
        await _authorize(username, active_role, parsed, request)
        credentials = await connection_service.write_credentials(connection)
        if credentials is not None:
            secrets = (credentials.username, credentials.password)
        if credentials is None:
            raise WriteRefused(
                f"{parsed.catalog} has no write connection yet; a security admin adds "
                "one to its connection in Catalog"
            )
        result = await source.write_rows(endpoint_of(connection), credentials, request)
    except WritebackDenied as exc:
        await audit.record("DENIED", str(exc))
        raise
    except WriteRefused as exc:
        await audit.record("ERROR", str(exc))
        raise
    except Exception as exc:
        detail = _masked(str(exc), secrets)
        logger.warning("Write-back to %s failed: %s: %s", parsed, type(exc).__name__, detail)
        await audit.record("ERROR", f"{type(exc).__name__}: {detail[:500]}")
        first = detail.strip().splitlines()[0][:300] if detail.strip() else type(exc).__name__
        raise WriteRefused(f"{parsed.catalog} refused the write: {first}") from None
    await audit.record("SUCCESS", None, result)
    return result


async def _authorize(
    username: str, active_role: str, target: WriteTarget, request: WriteRequest
) -> None:
    from app.modules.access_control.service import access_control_service

    if not active_role:
        raise WritebackDenied("A write needs an active role")
    needed = required_access(request.mode, request.when_matched, request.when_not_matched)
    allowed = await access_control_service.role_allows(
        principal=username,
        active_role=active_role,
        requests=[
            AccessRequest(access=access, catalog=target.catalog, database=target.schema,
                          table=target.table)
            for access in needed
        ],
    )
    refused = [access.upper() for access, ok in zip(needed, allowed, strict=True) if not ok]
    if refused:
        raise WritebackDenied(
            f"Role {active_role} may not {' or '.join(refused)} on {target}"
        )


def _masked(text: str, secrets: tuple[str, ...]) -> str:
    """A driver error without the write credential or credential-shaped text."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    try:
        return redact_sql_credentials(text)
    except CredentialsRedactionError:
        return "the source returned an error that could not be shown safely"


class _Audit:
    def __init__(
        self, username: str, role: str, target: str, mode: str, session_id: str | None
    ) -> None:
        self.username, self.role, self.target = username, role, target
        self.mode, self.session_id = mode, session_id

    async def record(
        self, status: str, error: str | None, result: WriteResult | None = None
    ) -> None:
        try:
            await write_audit_log(
                event_type="catalog_writeback",
                user_name=self.username,
                action=f"WRITE_{self.mode.upper()}",
                object_type="TABLE",
                object_name=self.target,
                status=status,
                error_message=error,
                rows_affected=(
                    result.inserted + result.updated + result.deleted if result else None
                ),
                session_id=self.session_id,
                active_role=self.role,
            )
        except Exception:  # noqa: BLE001 - the write's outcome stands without its audit row
            logger.warning("Could not audit a write-back to %s", self.target)
