"""Migration Connector repository — read-only source metadata enumeration.

Every query here runs against the **source** StarRocks deployment through an
``asyncmy`` connection built from the credentials supplied to the connect step.
Nothing in this module writes, and nothing returns a raw statement that could
carry a credential: the caller redacts before anything leaves the service.

Enumerations follow the NOVA-84 research surfaces exactly:

- tables/views: ``information_schema.tables`` (``TABLE_TYPE`` is reliable).
- materialized views: ``information_schema.materialized_views`` — **not**
  ``information_schema.tables``, which cannot distinguish an MV from a view.
- tasks/pipes/functions: their own ``information_schema`` views.
- masking / row-access policies: **no** metadata view and no ``SHOW CREATE``;
  their absence from the catalog is the finding, and the audit surfaces them
  from the policy listings rather than pretending they moved.
"""

from __future__ import annotations

from typing import Any

import asyncmy
import asyncmy.cursors

from app.core.security import decrypt_password


class SourceConnectionError(ValueError):
    """The source deployment could not be reached or authenticated against."""


class MigrationRepository:
    """Read-only metadata access against a source StarRocks deployment."""

    async def _connect(
        self,
        *,
        host: str,
        port: int,
        username: str,
        encrypted_password: str,
        database: str | None = None,
    ) -> asyncmy.Connection:
        try:
            return await asyncmy.connect(
                host=host,
                port=port,
                user=username,
                password=decrypt_password(encrypted_password),
                db=database,
                autocommit=True,
                connect_timeout=10,
            )
        except Exception as exc:  # noqa: BLE001 — reprojected without secrets
            raise SourceConnectionError(
                "Could not connect to the source StarRocks deployment"
            ) from exc

    async def _fetch(
        self,
        conn: asyncmy.Connection,
        sql: str,
    ) -> list[tuple[Any, ...]]:
        async with conn.cursor() as cur:
            await cur.execute(sql)
            if not cur.description:
                return []
            return list(await cur.fetchall())

    async def server_version(self, *, host: str, port: int, username: str,
                             encrypted_password: str) -> str | None:
        conn = await self._connect(
            host=host, port=port, username=username,
            encrypted_password=encrypted_password,
        )
        try:
            rows = await self._fetch(conn, "SELECT VERSION()")
            return str(rows[0][0]) if rows else None
        finally:
            conn.close()

    async def list_databases(
        self, *, host: str, port: int, username: str, encrypted_password: str
    ) -> list[str]:
        conn = await self._connect(
            host=host, port=port, username=username,
            encrypted_password=encrypted_password,
        )
        try:
            rows = await self._fetch(conn, "SHOW DATABASES")
        finally:
            conn.close()
        return [
            str(row[0])
            for row in rows
            if str(row[0]) not in ("_statistics_", "information_schema", "sys")
        ]

    async def list_tables(
        self,
        database: str,
        *,
        host: str,
        port: int,
        username: str,
        encrypted_password: str,
    ) -> list[str]:
        conn = await self._connect(
            host=host, port=port, username=username,
            encrypted_password=encrypted_password, database=database,
        )
        try:
            rows = await self._fetch(
                conn,
                "SELECT TABLE_NAME FROM information_schema.tables "
                f"WHERE TABLE_SCHEMA = '{_escape(database)}' "
                "AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME",
            )
        finally:
            conn.close()
        return [str(row[0]) for row in rows]

    async def list_views(
        self,
        database: str,
        *,
        host: str,
        port: int,
        username: str,
        encrypted_password: str,
    ) -> list[str]:
        conn = await self._connect(
            host=host, port=port, username=username,
            encrypted_password=encrypted_password, database=database,
        )
        try:
            rows = await self._fetch(
                conn,
                "SELECT TABLE_NAME FROM information_schema.tables "
                f"WHERE TABLE_SCHEMA = '{_escape(database)}' "
                "AND TABLE_TYPE = 'VIEW' ORDER BY TABLE_NAME",
            )
        finally:
            conn.close()
        return [str(row[0]) for row in rows]

    async def list_materialized_views(
        self,
        database: str,
        *,
        host: str,
        port: int,
        username: str,
        encrypted_password: str,
    ) -> list[dict[str, str]]:
        """List MVs from ``information_schema.materialized_views``.

        Returns ``{"name", "refresh_mode"}``. The refresh mode is the one bit
        of MV state the dry-run needs beyond the name: a **sync** MV cannot
        carry its refresh/partition/properties through any export surface, so
        it is always ``lossy`` (NOVA-84 Ruling B).
        """
        conn = await self._connect(
            host=host, port=port, username=username,
            encrypted_password=encrypted_password, database=database,
        )
        try:
            rows = await self._fetch(
                conn,
                "SELECT TABLE_NAME, REFRESH_MODE FROM "
                "information_schema.materialized_views "
                f"WHERE TABLE_SCHEMA = '{_escape(database)}' ORDER BY TABLE_NAME",
            )
        finally:
            conn.close()
        return [
            {"name": str(row[0]), "refresh_mode": str(row[1] or "")}
            for row in rows
        ]

    async def list_tasks(
        self,
        database: str,
        *,
        host: str,
        port: int,
        username: str,
        encrypted_password: str,
    ) -> list[str]:
        conn = await self._connect(
            host=host, port=port, username=username,
            encrypted_password=encrypted_password, database=database,
        )
        try:
            rows = await self._fetch(
                conn,
                "SELECT TASK_NAME FROM information_schema.tasks "
                f"WHERE DATABASE_NAME = '{_escape(database)}' ORDER BY TASK_NAME",
            )
        except Exception:
            # Engine without the `tasks` schema — treat as no tasks rather than
            # failing the whole assessment. The dry-run still reports the gap.
            return []
        finally:
            conn.close()
        return [str(row[0]) for row in rows]

    async def list_pipes(
        self,
        database: str,
        *,
        host: str,
        port: int,
        username: str,
        encrypted_password: str,
    ) -> list[str]:
        conn = await self._connect(
            host=host, port=port, username=username,
            encrypted_password=encrypted_password, database=database,
        )
        try:
            rows = await self._fetch(
                conn,
                "SELECT PIPE_NAME FROM information_schema.pipes "
                f"WHERE DATABASE_NAME = '{_escape(database)}' ORDER BY PIPE_NAME",
            )
        except Exception:
            return []
        finally:
            conn.close()
        return [str(row[0]) for row in rows]

    async def list_policy_names(
        self,
        table: str,
        database: str,
        *,
        host: str,
        port: int,
        username: str,
        encrypted_password: str,
    ) -> list[str]:
        """Best-effort policy name listing for the dry-run's ``skipped`` rows.

        StarRocks publishes no ``SHOW CREATE`` for masking/row-access policies.
        Whether an ``information_schema`` view exists at all varies by version,
        so an unknown table yields an empty list here rather than failing the
        assessment. The audit still reports the *category* gap through the
        absence of names; a deployment whose engine does expose the view gets
        per-policy rows.

        ``table`` is selected by the caller from a fixed pair of constants, not
        from request input, so it is not interpolated from untrusted data.
        """
        if table not in (
            "information_schema.masking_policies",
            "information_schema.row_access_policies",
        ):
            raise ValueError(f"Unsupported policy view: {table}")
        column = "POLICY_NAME" if "masking" in table else "POLICY_NAME"
        conn = await self._connect(
            host=host, port=port, username=username,
            encrypted_password=encrypted_password, database=database,
        )
        try:
            rows = await self._fetch(
                conn,
                f"SELECT {column} FROM {table} "
                f"WHERE TABLE_SCHEMA = '{_escape(database)}' "
                f"OR DATABASE_NAME = '{_escape(database)}' ORDER BY 1",
            )
        except Exception:
            return []
        finally:
            conn.close()
        return [str(row[0]) for row in rows]

    async def list_functions(
        self,
        database: str,
        *,
        host: str,
        port: int,
        username: str,
        encrypted_password: str,
    ) -> list[str]:
        conn = await self._connect(
            host=host, port=port, username=username,
            encrypted_password=encrypted_password, database=database,
        )
        try:
            rows = await self._fetch(
                conn,
                "SELECT FUNCTION_NAME FROM information_schema.routines "
                f"WHERE ROUTINE_SCHEMA = '{_escape(database)}' "
                "ORDER BY FUNCTION_NAME",
            )
        except Exception:
            return []
        finally:
            conn.close()
        return [str(row[0]) for row in rows]


def _escape(value: str) -> str:
    """Escape a database/user identifier for an ``information_schema`` literal.

    Identifier values are not user SQL: they arrive from ``SHOW DATABASES`` on
    the *source* and are interpolated into a metadata query. Doubling the quote
    is the only escape that matters here, and it keeps a database named
    ``a'b`` from breaking out of the literal.
    """
    return value.replace("\\", "\\\\").replace("'", "''")


migration_repo = MigrationRepository()
