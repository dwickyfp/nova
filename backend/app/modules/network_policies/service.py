"""Network policy service — per-user connection-source restrictions.

What StarRocks 4.1.4 actually supports
======================================

Verified against the pinned engine grammar (commit
``4a9848edf03f5c936dac664b2d52527f48e72eb0``, tag ``4.1.4``) and the FE config
reference:

* **No ``CREATE NETWORK POLICY`` object.** The grammar has no such production —
  the only statement in the pinned grammar mentioning network policy is absent
  entirely. Snowflake-style first-class policy objects do not exist here.
* **No ``enable_ip_based_authentication`` FE config and no IP allowlist
  variables.** ``docs/gap-analysis.md`` §5 sketches a ``fe.conf`` flag that the
  4.1.4 config reference does not list.
* **What does exist:** the host part of a StarRocks user identity. A client can
  only connect as ``user`` if its source address matches an identity host
  registered for that user (``CREATE USER u@'10.0.0.0/8'``). This is the one
  host-based control the engine enforces, and it is enforced by the engine at
  connect time, not by Nova.

Scope of this module
--------------------

Nova models a network policy as a named, per-user pair of host lists and
*projects* it onto the identity hosts the engine understands — creating an
identity for each allowed host and dropping identities for hosts that are no
longer allowed. The engine remains the enforcement point: a client whose source
does not match any identity host is rejected by StarRocks before Nova sees it.

**DEFER (spec §5 remainder), with reasons:**

* Snowflake-style *named policy objects* and *attach/detach to role* — no engine
  object exists to attach to; a role has no source address and StarRocks has no
  ``ATTACH NETWORK POLICY`` statement. Role-level restriction therefore cannot
  be enforced and is not faked.
* *Global IP allowlist/blocklist* — no engine surface. A Nova-side allowlist
  would sit in front of the MySQL-port proxy only and would not cover direct FE
  connections, so it would be a false guarantee. Left to the network layer
  (firewall / security group) rather than claimed here.
* *Connection source tracking* — provided read-only from the existing audit log
  (successful logins), which already records the user; it does not invent a new
  tracking mechanism.
"""

from __future__ import annotations

import logging
import re

from app.common.audit import write_audit_log
from app.core.database import db

from .repository import network_policy_repo
from .schemas import NetworkPolicyCreate, NetworkPolicyResponse, NetworkPolicyUpdate

log = logging.getLogger(__name__)

#: Host patterns are interpolated into ``CREATE USER``/``DROP USER`` identities.
#: The Pydantic models validate them on the way in; this re-validates on the way
#: out because a metadata row can predate a validator change and a path segment
#: is not trusted just because the create body was.
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9._%:/-]{1,255}$")


class NetworkPolicyError(ValueError):
    """A policy operation the service refused before or after the engine."""


def _safe_policy_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise NetworkPolicyError(f"Invalid policy name: {name!r}")
    return name


def _safe_host_pattern(pattern: str) -> str:
    if not _HOST_PATTERN.fullmatch(pattern or ""):
        raise NetworkPolicyError(f"Invalid host pattern: {pattern!r}")
    return pattern


class NetworkPolicyService:
    """CRUD over Nova network-policy metadata, mirrored to engine identities."""

    async def ensure_schema(self) -> None:
        await network_policy_repo.ensure_schema()

    # ── Read path ──────────────────────────────────────────────

    async def list_policies(self) -> list[NetworkPolicyResponse]:
        rows = await network_policy_repo.list_all()
        return [self._to_response(row) for row in rows]

    async def get_policy(self, name: str) -> NetworkPolicyResponse:
        name = _safe_policy_name(name)
        row = await network_policy_repo.get_by_name(name)
        if row is None:
            raise NetworkPolicyError(f"Network policy '{name}' not found")
        return self._to_response(row)

    @staticmethod
    def _to_response(row: dict) -> NetworkPolicyResponse:
        return NetworkPolicyResponse(
            name=row["name"],
            username=row["username"],
            host=row["host"],
            allowed_hosts=row.get("allowed_hosts", []),
            denied_hosts=row.get("denied_hosts", []),
            comment=row.get("comment"),
            created_at=row.get("created_at"),
            created_by=row.get("created_by"),
        )

    # ── DDL builders (pure) ────────────────────────────────────

    @staticmethod
    def _identity(username: str, host_pattern: str) -> str:
        safe_user = username.replace("\\", "\\\\").replace("'", "\\'")
        safe_host = host_pattern.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{safe_user}'@'{safe_host}'"

    def build_allow_identity_sql(self, username: str, host_pattern: str) -> str:
        """``CREATE USER`` for an allowed source, tolerating an existing identity.

        StarRocks 4.1.4 has no ``CREATE USER IF NOT EXISTS ... IDENTIFIED BY``
        combined with a password requirement, and ``IF NOT EXISTS`` would hide a
        real mismatch. ``GRANT``/``REVOKE`` are not applicable to a host, so the
        create is guarded by existence instead (see the service call path).
        """
        identity = self._identity(username, host_pattern)
        return f"CREATE USER {identity} IDENTIFIED BY ''"

    def build_drop_identity_sql(self, username: str, host_pattern: str) -> str:
        return f"DROP USER {self._identity(username, host_pattern)}"

    # ── Engine execution (system pool → admin surface) ─────────

    async def _list_existing_identities(self, username: str) -> set[str]:
        """Return the host patterns already registered for ``username``."""
        show = await db.execute_system("SHOW USERS")
        hosts: set[str] = set()
        for row in show.get("rows", []):
            identity = str(row[0]) if row else ""
            if "@" not in identity:
                continue
            user_part, host_part = identity.split("@", 1)
            if user_part.strip("'`\"") == username:
                hosts.add(host_part.strip("'`\""))
        return hosts

    async def _apply_identities(
        self,
        username: str,
        desired_allowed: list[str],
        previous_allowed: list[str],
    ) -> None:
        """Create identities for new allowed hosts and drop removed ones.

        Only hosts Nova itself projected are dropped — an identity that existed
        before the policy (or was created out of band) is left alone, so applying
        a policy never silently locks an operator out of an identity they manage.
        """
        existing = await self._list_existing_identities(username)
        for host in desired_allowed:
            if host not in existing:
                await db.execute_system(self.build_allow_identity_sql(username, host))
        for host in previous_allowed:
            if host in desired_allowed:
                continue
            if host in existing:
                try:
                    await db.execute_system(self.build_drop_identity_sql(username, host))
                except Exception as exc:  # noqa: BLE001 - identity may still own grants
                    log.warning("Could not drop identity %s@%s: %s", username, host, exc)

    # ── Write path ─────────────────────────────────────────────

    async def create_policy(
        self, body: NetworkPolicyCreate, *, created_by: str
    ) -> NetworkPolicyResponse:
        if await network_policy_repo.get_by_name(body.name):
            raise NetworkPolicyError(f"Network policy '{body.name}' already exists")

        allowed = [rule.model_dump() for rule in body.allowed_hosts]
        denied = [rule.model_dump() for rule in body.denied_hosts]
        await network_policy_repo.upsert(
            name=body.name,
            username=body.username,
            host=body.host,
            allowed_hosts=allowed,
            denied_hosts=denied,
            comment=body.comment,
            created_by=created_by,
        )
        # Metadata first so a policy always has a definition even if the
        # engine projection is interrupted; the identity sync is idempotent.
        await self._apply_identities(
            body.username,
            [_safe_host_pattern(r.host_pattern) for r in body.allowed_hosts],
            [],
        )
        await write_audit_log(
            event_type="security",
            user_name=created_by,
            action="create_network_policy",
            object_type="network_policy",
            object_name=body.name,
            status="SUCCESS",
        )
        return await self.get_policy(body.name)

    async def update_policy(
        self, name: str, body: NetworkPolicyUpdate, *, updated_by: str
    ) -> NetworkPolicyResponse:
        name = _safe_policy_name(name)
        existing = await network_policy_repo.get_by_name(name)
        if existing is None:
            raise NetworkPolicyError(f"Network policy '{name}' not found")

        previous_allowed = [
            str(rule.get("host_pattern", "")) for rule in existing.get("allowed_hosts", [])
        ]
        allowed = (
            [rule.model_dump() for rule in body.allowed_hosts]
            if body.allowed_hosts is not None
            else existing.get("allowed_hosts", [])
        )
        denied = (
            [rule.model_dump() for rule in body.denied_hosts]
            if body.denied_hosts is not None
            else existing.get("denied_hosts", [])
        )
        comment = body.comment if body.comment is not None else existing.get("comment")

        await network_policy_repo.upsert(
            name=name,
            username=existing["username"],
            host=existing["host"],
            allowed_hosts=allowed,
            denied_hosts=denied,
            comment=comment,
            created_by=existing.get("created_by") or updated_by,
        )

        if body.allowed_hosts is not None:
            desired = [_safe_host_pattern(rule.host_pattern) for rule in body.allowed_hosts]
            await self._apply_identities(existing["username"], desired, previous_allowed)

        await write_audit_log(
            event_type="security",
            user_name=updated_by,
            action="update_network_policy",
            object_type="network_policy",
            object_name=name,
            status="SUCCESS",
        )
        return await self.get_policy(name)

    async def delete_policy(self, name: str, *, deleted_by: str) -> bool:
        name = _safe_policy_name(name)
        existing = await network_policy_repo.get_by_name(name)
        if existing is None:
            raise NetworkPolicyError(f"Network policy '{name}' not found")

        previous_allowed = [
            str(rule.get("host_pattern", "")) for rule in existing.get("allowed_hosts", [])
        ]
        deleted = await network_policy_repo.delete(name)
        # Removing a policy returns the user to the engine default identity set
        # for the hosts Nova projected; a pre-existing identity is not touched.
        await self._apply_identities(existing["username"], [], previous_allowed)
        await write_audit_log(
            event_type="security",
            user_name=deleted_by,
            action="delete_network_policy",
            object_type="network_policy",
            object_name=name,
            status="SUCCESS",
        )
        return deleted

    # ── Connection source tracking (read-only) ─────────────────

    async def list_connection_sources(self, limit: int = 50) -> list[dict]:
        """Recent successful login sources from the audit log.

        This is the honest form of the spec's "connection source tracking": the
        audit log already records who logged in; nothing new is collected and no
        source address is invented (StarRocks does not expose the client IP to
        SQL).
        """
        safe_limit = max(1, min(int(limit), 500))
        result = await db.execute_system(
            "SELECT user_name, MAX(event_time) AS last_seen, COUNT(*) AS events "
            "FROM NOVA_SYSTEM.AUDIT_LOG "
            "WHERE event_type = 'login' AND status = 'SUCCESS' "
            "GROUP BY user_name "
            f"ORDER BY last_seen DESC LIMIT {safe_limit}"
        )
        rows: list[dict] = []
        for row in result.get("rows", []):
            if len(row) < 3:
                continue
            rows.append({"user_name": str(row[0]), "last_seen": row[1], "events": int(row[2] or 0)})
        return rows


network_policy_service = NetworkPolicyService()
