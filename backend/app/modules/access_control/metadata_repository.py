"""Nova's access-control configuration in PostgreSQL.

The prepared replacement for the ``NOVA_SYSTEM`` paths of
:class:`AccessControlService` (role projections, managed policy state, data
scope assignments) and of ``business_policy.py``, switched in at the
all-domain cutover. These rows record what Nova asked Ranger and StarRocks to
do and how far that got. They are never an authorization source: Ranger and
the patched FE decide every user-data read.

Every change runs in the caller's actor-bound metadata transaction, names the
revision it read, is refused unless the active role administers access control
(``ACCOUNTADMIN`` for business policy), and writes a success audit row in the
same transaction. A concurrent change from the same revision is a conflict,
never a silent overwrite.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.domain import ProjectionState
from app.modules.access_control.security_context import SecurityContext

SECURITY_ADMIN_ROLES = frozenset({"ACCOUNTADMIN", "SECURITYADMIN", "security_admin"})


def data_scope_assignment_id(principal: str, role: str, dimension: str) -> str:
    """The source's assignment identity: one row per principal, role and dimension."""
    return hashlib.sha256(f"{principal}\x1f{role}\x1f{dimension}".encode()).hexdigest()


def _revision(value: int | None) -> int | None:
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError("A positive revision is required")
    return value


class AccessControlMetadataRepository:
    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
    ) -> None:
        self.connection = connection
        self.actor = actor

    async def _bound(self, *, administer: bool = False) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Access-control metadata requires an active metadata transaction")
        if administer and self.actor.active_role not in SECURITY_ADMIN_ROLES:
            raise MetadataPermissionDenied()
        cursor = await self.connection.execute(
            "SELECT current_setting('nova.principal',true)=%s "
            "AND current_setting('nova.active_role',true)=%s "
            "AND current_setting('nova.security_context_version',true)=%s "
            "AND current_setting('nova.service_identity',true)=current_user AS valid",
            (self.actor.principal, self.actor.active_role,
             str(self.actor.security_context_version)),
        )
        row = await cursor.fetchone()
        if not row or row["valid"] is not True:
            raise MetadataPermissionDenied()

    async def _one(self, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        cursor = await self.connection.execute(query, params)
        return await cursor.fetchone()

    async def _put(
        self, table: str, key: str, identity: str, values: dict[str, Any],
        expected_revision: int | None,
    ) -> dict[str, Any]:
        """Create (``expected_revision=None``) or change a row from the revision read."""
        await self._bound(administer=True)
        if _revision(expected_revision) is None:
            columns = (key, *values)
            row = await self._one(
                f"INSERT INTO nova_meta.{table} ({','.join(columns)}) VALUES ("
                + ",".join(["%s"] * len(columns)) + ") RETURNING *",
                (identity, *values.values()),
            )
        else:
            assignments = ",".join(f"{column}=%s" for column in values)
            row = await self._one(
                f"UPDATE nova_meta.{table} SET {assignments},"
                f"metadata_revision=metadata_revision+1 WHERE {key}=%s "
                "AND metadata_revision=%s RETURNING *",
                (*values.values(), identity, expected_revision),
            )
        if row is None:
            raise MetadataConflict()
        return row

    async def _delete(self, table: str, key: str, identity: str, expected_revision: int) -> None:
        await self._bound(administer=True)
        cursor = await self.connection.execute(
            f"DELETE FROM nova_meta.{table} WHERE {key}=%s AND metadata_revision=%s",
            (identity, _revision(expected_revision)),
        )
        if cursor.rowcount != 1:
            raise MetadataConflict()

    # ── Role projections ──────────────────────────────────────────────────

    async def get_role_projection(self, role: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_security_role_projections WHERE role_name=%s",
            (role,),
        )

    async def put_role_projection(
        self, role: str, state: ProjectionState, *, message: str | None = None,
        expected_revision: int | None,
    ) -> dict[str, Any]:
        exists = state in {ProjectionState.ACTIVE, ProjectionState.PROPAGATING}
        return await self._put(
            "config_security_role_projections", "role_name", role,
            {"state": state.value, "marker_exists": exists, "ranger_exists": exists,
             "message": (message or "")[:2048] or None},
            expected_revision,
        )

    async def delete_role_projection(self, role: str, *, expected_revision: int) -> None:
        await self._delete("config_security_role_projections", "role_name", role,
                           expected_revision)

    # ── Managed policy state ──────────────────────────────────────────────

    async def get_policy_state(self, name: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_security_policies WHERE policy_name=%s", (name,),
        )

    async def put_policy_state(
        self, name: str, ranger_result: dict[str, Any], state: ProjectionState, *,
        expected_revision: int | None,
    ) -> dict[str, Any]:
        desired = json.dumps(ranger_result, sort_keys=True, default=str)
        return await self._put(
            "config_security_policies", "policy_name", name,
            {"ranger_id": ranger_result.get("id"), "ranger_guid": ranger_result.get("guid"),
             "state": state.value, "desired_hash": hashlib.sha256(desired.encode()).hexdigest()},
            expected_revision,
        )

    # ── Data scope assignments ────────────────────────────────────────────

    async def get_data_scope(
        self, principal: str, role: str, dimension: str,
    ) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_data_scope_assignments WHERE assignment_id=%s",
            (data_scope_assignment_id(principal, role, dimension),),
        )

    async def put_data_scope(
        self, principal: str, role: str, dimension: str, values: list[str], *,
        state: ProjectionState = ProjectionState.PROPAGATING, expected_revision: int | None,
    ) -> dict[str, Any]:
        if not all(isinstance(value, str) for value in values):
            raise ValueError("Scope values are strings")
        identity = data_scope_assignment_id(principal, role, dimension)
        payload: dict[str, Any] = {"scope_values_json": json.dumps(sorted(set(values))),
                                   "state": state.value}
        if expected_revision is None:
            payload = {"principal": principal, "role_name": role, "dimension_key": dimension,
                       **payload}
        return await self._put("config_data_scope_assignments", "assignment_id", identity,
                               payload, expected_revision)

    # ── Health ────────────────────────────────────────────────────────────

    async def health_counts(self) -> dict[str, int]:
        await self._bound()
        row = await self._one(
            "SELECT (SELECT count(*) FROM nova_meta.config_security_policies "
            "WHERE state IN ('PENDING','PROPAGATING')) AS pending_propagation,"
            "(SELECT count(*) FROM nova_meta.config_security_role_projections "
            "WHERE state='DRIFTED') AS drift_count", (),
        )
        assert row is not None
        return {key: int(value) for key, value in row.items()}

    # ── Business policy ───────────────────────────────────────────────────

    async def current_business_policy(self) -> dict[str, Any] | None:
        """The newest revision in force, or None when only the default applies.

        Two imported revisions with the same number are an unreconciled source
        race and are refused rather than picked.
        """
        await self._bound()
        cursor = await self.connection.execute(
            "SELECT * FROM nova_meta.config_business_policies WHERE policy_key='default' "
            "ORDER BY revision DESC LIMIT 2", (),
        )
        rows = list(await cursor.fetchall())
        if len(rows) == 2 and rows[0]["revision"] == rows[1]["revision"]:
            raise MetadataConflict()
        return rows[0] if rows else None

    async def save_business_policy(self, definition: dict[str, Any]) -> dict[str, Any]:
        """Store ``definition`` as the revision after the one it was edited from.

        ``definition['revision']`` is the revision the editor read; the stored
        row carries the next one. Only ACCOUNTADMIN saves policy.
        """
        await self._bound()
        if self.actor.active_role != "ACCOUNTADMIN":
            raise MetadataPermissionDenied()
        read = _revision(definition.get("revision"))
        if read is None:
            raise ValueError("The policy revision that was edited is required")
        current = await self.current_business_policy()
        if (current["revision"] if current else 1) != read:
            raise MetadataConflict()
        stored = {**definition, "id": "default", "revision": read + 1}
        digest = hashlib.sha256(
            json.dumps(stored, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:32]
        row = await self._one(
            "INSERT INTO nova_meta.config_business_policies (id,revision,definition) "
            "VALUES (%s,%s,%s) RETURNING *", ("default:" + digest, read + 1, Jsonb(stored)),
        )
        assert row is not None
        return row
