"""Network policy metadata repository — NOVA_SYSTEM.CONFIG_NETWORK_POLICIES.

StarRocks 4.1.4 has no network-policy object, so Nova is the source of truth for
the policy definition and mirrors it into engine identities (``user@'host'``).
The rows here hold host patterns only — **no credential-bearing column exists**
(AGENTS.md §2).
"""

from __future__ import annotations

import json
from uuid import uuid4

import asyncmy
import asyncmy.cursors

from app.core.config import settings

CONFIG_NETWORK_POLICIES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_NETWORK_POLICIES (
    id            VARCHAR(64) NOT NULL,
    name          VARCHAR(128) NOT NULL,
    username      VARCHAR(128) NOT NULL,
    host          VARCHAR(255) NOT NULL,
    allowed_hosts TEXT,
    denied_hosts  TEXT,
    comment       VARCHAR(1024),
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by    VARCHAR(128)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""


class NetworkPolicyRepository:
    """Persist network-rule metadata. Never stores a secret."""

    @staticmethod
    async def _connect() -> asyncmy.Connection:
        return await asyncmy.connect(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user=settings.STARROCKS_ROOT_USER,
            password=settings.STARROCKS_ROOT_PASSWORD,
            autocommit=True,
        )

    async def ensure_schema(self) -> None:
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(CONFIG_NETWORK_POLICIES_DDL)
        finally:
            conn.close()

    async def list_all(self) -> list[dict]:
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, name, username, host, allowed_hosts, denied_hosts, "
                    "comment, created_at, created_by "
                    "FROM NOVA_SYSTEM.CONFIG_NETWORK_POLICIES ORDER BY name"
                )
                return [self._row_to_dict(row) for row in await cur.fetchall()]
        finally:
            conn.close()

    async def get_by_name(self, name: str) -> dict | None:
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, name, username, host, allowed_hosts, denied_hosts, "
                    "comment, created_at, created_by "
                    "FROM NOVA_SYSTEM.CONFIG_NETWORK_POLICIES WHERE name = %s",
                    (name,),
                )
                row = await cur.fetchone()
                return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    async def delete(self, name: str) -> bool:
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM NOVA_SYSTEM.CONFIG_NETWORK_POLICIES WHERE name = %s",
                    (name,),
                )
                return cur.rowcount > 0
        finally:
            conn.close()

    async def upsert(
        self,
        *,
        name: str,
        username: str,
        host: str,
        allowed_hosts: list[dict],
        denied_hosts: list[dict],
        comment: str | None,
        created_by: str,
    ) -> None:
        """Insert or replace the row for ``name`` (policy names are unique)."""
        existing = await self.get_by_name(name)
        allowed_json = json.dumps(allowed_hosts or [])
        denied_json = json.dumps(denied_hosts or [])
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                if existing:
                    await cur.execute(
                        "UPDATE NOVA_SYSTEM.CONFIG_NETWORK_POLICIES SET "
                        "username = %s, host = %s, allowed_hosts = %s, denied_hosts = %s, "
                        "comment = %s WHERE name = %s",
                        (username, host, allowed_json, denied_json, comment, name),
                    )
                else:
                    await cur.execute(
                        "INSERT INTO NOVA_SYSTEM.CONFIG_NETWORK_POLICIES "
                        "(id, name, username, host, allowed_hosts, denied_hosts, "
                        "comment, created_at, created_by) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s)",
                        (
                            str(uuid4()),
                            name,
                            username,
                            host,
                            allowed_json,
                            denied_json,
                            comment,
                            created_by,
                        ),
                    )
        finally:
            conn.close()

    @staticmethod
    def _parse_rules(raw: object) -> list[dict]:
        if not raw:
            return []
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict)]

    @classmethod
    def _row_to_dict(cls, row: dict) -> dict:
        data = dict(row)
        data["allowed_hosts"] = cls._parse_rules(data.pop("allowed_hosts", None))
        data["denied_hosts"] = cls._parse_rules(data.pop("denied_hosts", None))
        return data


network_policy_repo = NetworkPolicyRepository()
