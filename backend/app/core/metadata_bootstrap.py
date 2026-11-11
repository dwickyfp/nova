"""Provision restricted PostgreSQL identities before the schema installer runs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from pydantic import SecretStr

from app.core.metadata_db import (
    METADATA_DEPLOYMENT_LOCK,
    MetadataOperationFailed,
    MetadataOutcomeUnknown,
    MetadataPermissionDenied,
    MetadataPoolConfig,
    MetadataSchemaMismatch,
    MetadataTimeout,
)
from app.core.metadata_migrations import METADATA_ROLES

WORKLOAD_ROLES = tuple(f"nova_meta_{name}" for name in (
    "web", "query", "proxy", "agent", "scheduler", "task", "ingestion", "ml",
    "notebook", "automation", "maintenance", "outbox",
))
LOGIN_ROLES = ("nova_meta_migrator", "nova_meta_readonly", "nova_meta_backup", *WORKLOAD_ROLES)
ALL_ROLES = (*METADATA_ROLES, *WORKLOAD_ROLES)


class BootstrapConfigurationError(ValueError):
    def __init__(self) -> None:
        super().__init__("Invalid metadata bootstrap configuration")


class BootstrapVerificationFailed(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Provisioned metadata identities could not be verified")


@dataclass(frozen=True, slots=True)
class BootstrapCredentials:
    passwords: Mapping[str, SecretStr] = field(repr=False)

    def __post_init__(self) -> None:
        if set(self.passwords) != set(LOGIN_ROLES):
            raise BootstrapConfigurationError()
        values = []
        for password in self.passwords.values():
            if not isinstance(password, SecretStr):
                raise BootstrapConfigurationError()
            value = password.get_secret_value()
            if (
                not 24 <= len(value) <= 256 or "\x00" in value
                or any(marker in value.casefold() for marker in ("change_me", "replace_me"))
            ):
                raise BootstrapConfigurationError()
            values.append(value)
        if len(set(values)) != len(values):
            raise BootstrapConfigurationError()
        object.__setattr__(self, "passwords", MappingProxyType(dict(self.passwords)))

    @classmethod
    def from_json(cls, raw: SecretStr) -> BootstrapCredentials:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise BootstrapConfigurationError()
                result[key] = value
            return result

        try:
            value = raw.get_secret_value()
            if len(value) > 16384:
                raise BootstrapConfigurationError()
            parsed = json.loads(value, object_pairs_hook=unique)
            if not isinstance(parsed, dict) or any(not isinstance(v, str) for v in parsed.values()):
                raise BootstrapConfigurationError()
            return cls({name: SecretStr(password) for name, password in parsed.items()})
        except (ValueError, TypeError, AttributeError):
            raise BootstrapConfigurationError() from None


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    created: bool
    login_count: int


async def _verify_roles(conn: psycopg.AsyncConnection[dict[str, Any]], marker: str) -> None:
    cursor = await conn.execute(
        "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
        "rolreplication, rolbypassrls, rolconfig, shobj_description(oid, 'pg_authid') AS marker "
        "FROM pg_roles WHERE rolname = ANY(%s)", (list(ALL_ROLES),),
    )
    roles = {row["rolname"]: row for row in await cursor.fetchall()}
    if set(roles) != set(ALL_ROLES):
        raise MetadataSchemaMismatch()
    for name, attributes in roles.items():
        if (
            attributes["marker"] != marker or attributes["rolcanlogin"] != (name in LOGIN_ROLES)
            or attributes["rolreplication"] != (name == "nova_meta_backup")
            or attributes["rolconfig"] is not None
            or any(attributes[key] for key in (
                "rolsuper", "rolcreatedb", "rolcreaterole", "rolbypassrls",
            ))
        ):
            raise MetadataSchemaMismatch()
    cursor = await conn.execute(
        "SELECT bool_and(CASE WHEN rolname=ANY(%s) THEN "
        "coalesce(rolpassword LIKE 'SCRAM-SHA-256$%%', false) "
        "ELSE rolpassword IS NULL END) AS valid FROM pg_authid WHERE rolname=ANY(%s)",
        (list(LOGIN_ROLES), list(ALL_ROLES)),
    )
    verifiers = await cursor.fetchone()
    if not verifiers or not verifiers["valid"]:
        raise MetadataSchemaMismatch()
    cursor = await conn.execute(
        "SELECT EXISTS(SELECT 1 FROM pg_db_role_setting s JOIN pg_roles r ON r.oid=s.setrole "
        "WHERE r.rolname=ANY(%s)) AS configured", (list(ALL_ROLES),),
    )
    settings = await cursor.fetchone()
    if not settings or settings["configured"]:
        raise MetadataSchemaMismatch()
    cursor = await conn.execute(
        "SELECT coalesce(r.rolname, 'PUBLIC') AS grantee, a.privilege_type, a.is_grantable "
        "FROM pg_database d CROSS JOIN LATERAL "
        "aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) a "
        "LEFT JOIN pg_roles r ON r.oid=a.grantee "
        "WHERE d.datname=current_database() AND a.grantee <> d.datdba"
    )
    actual_acl = {
        (r["grantee"], r["privilege_type"], r["is_grantable"]) for r in await cursor.fetchall()
    }
    expected_acl = {("nova_meta_owner", "CREATE", False)}
    expected_acl.update((role, "CONNECT", False) for role in LOGIN_ROLES)
    if actual_acl != expected_acl:
        raise MetadataSchemaMismatch()
    cursor = await conn.execute(
        "SELECT EXISTS(SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL "
        "aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) a "
        "WHERE n.nspname='public' AND a.grantee <> n.nspowner) AS exposed"
    )
    exposure = await cursor.fetchone()
    if not exposure or exposure["exposed"]:
        raise MetadataSchemaMismatch()
    cursor = await conn.execute(
        "SELECT granted.rolname AS granted, member.rolname AS member, "
        "m.admin_option, m.inherit_option, m.set_option FROM pg_auth_members m "
        "JOIN pg_roles granted ON granted.oid=m.roleid "
        "JOIN pg_roles member ON member.oid=m.member "
        "WHERE granted.rolname=ANY(%s) OR member.rolname=ANY(%s)",
        (list(ALL_ROLES), list(ALL_ROLES)),
    )
    actual = {
        (r["granted"], r["member"], r["admin_option"], r["inherit_option"], r["set_option"])
        for r in await cursor.fetchall()
    }
    expected = {("nova_meta_owner", "nova_meta_migrator", False, False, True)}
    expected.update(("nova_meta_app", role, False, True, False) for role in WORKLOAD_ROLES)
    if actual != expected:
        raise MetadataSchemaMismatch()
    for role in ALL_ROLES:
        cursor = await conn.execute(
            "SELECT has_database_privilege(%s, current_database(), 'CREATE') AS can_create, "
            "has_database_privilege(%s, current_database(), 'TEMP') AS can_temp, "
            "has_database_privilege(%s, current_database(), 'CONNECT') AS can_connect, "
            "has_schema_privilege(%s, 'public', 'USAGE') AS public_usage, "
            "has_schema_privilege(%s, 'public', 'CREATE') AS public_create",
            (role, role, role, role, role),
        )
        privileges = await cursor.fetchone()
        if not privileges:
            raise MetadataSchemaMismatch()
        if (
            privileges["can_create"] != (role == "nova_meta_owner")
            or privileges["can_connect"] != (role in LOGIN_ROLES)
            or privileges["can_temp"] or privileges["public_usage"] or privileges["public_create"]
        ):
            raise MetadataSchemaMismatch()


async def bootstrap(
    config: MetadataPoolConfig, credentials: BootstrapCredentials, *, deployment_id: UUID,
) -> BootstrapResult:
    """Create or verify this deployment's identities without adopting existing roles."""
    if not isinstance(deployment_id, UUID) or deployment_id.int == 0:
        raise BootstrapConfigurationError()
    admin_password = conninfo_to_dict(config.dsn).get("password")
    if admin_password and any(
        value.get_secret_value() == admin_password for value in credentials.passwords.values()
    ):
        raise BootstrapConfigurationError()
    marker = f"nova-control-bootstrap:v1:{deployment_id}"
    created = False
    try:
        async with await psycopg.AsyncConnection.connect(
            config.dsn, autocommit=True, row_factory=dict_row, prepare_threshold=None,
            application_name="nova-metadata-bootstrap",
            connect_timeout=max(1, int(config.acquire_timeout)),
            options=(
                f"-c statement_timeout={config.statement_timeout_ms} "
                f"-c lock_timeout={config.lock_timeout_ms} "
                f"-c idle_in_transaction_session_timeout={config.idle_transaction_timeout_ms} "
                "-c synchronous_commit=on -c timezone=UTC"
            ),
        ) as conn:
            if conn.info.server_version // 10000 != 17 or not conn.pgconn.used_password:
                raise MetadataSchemaMismatch()
            async with conn.transaction():
                cursor = await conn.execute(
                    "SELECT r.rolsuper AND d.datdba=r.oid AS allowed, "
                    "shobj_description(d.oid, 'pg_database') AS marker FROM pg_database d "
                    "JOIN pg_roles r ON r.rolname=session_user WHERE d.datname=current_database()"
                )
                database = await cursor.fetchone()
                if not database or not database["allowed"]:
                    raise MetadataSchemaMismatch()
                # Even hashed credentials must stay out of statement/error logs.
                await conn.execute("SET LOCAL log_statement = 'none'")
                await conn.execute("SET LOCAL log_min_error_statement = 'panic'")
                await conn.execute("SET LOCAL log_parameter_max_length_on_error = 0")
                await conn.execute("SET LOCAL log_min_duration_statement = -1")
                await conn.execute("SET LOCAL log_min_duration_sample = -1")
                await conn.execute("SET LOCAL log_transaction_sample_rate = 0")
                await conn.execute("SET LOCAL search_path = pg_catalog")
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(%s)", (METADATA_DEPLOYMENT_LOCK,),
                )
                cursor = await conn.execute(
                    "SELECT shobj_description(oid, 'pg_database') AS marker FROM pg_database "
                    "WHERE datname=current_database()"
                )
                database = await cursor.fetchone()
                if not database:
                    raise MetadataSchemaMismatch()
                cursor = await conn.execute(
                    "SELECT rolname FROM pg_roles WHERE rolname=ANY(%s)", (list(ALL_ROLES),),
                )
                existing = await cursor.fetchall()
                if not existing:
                    cursor = await conn.execute(
                        "SELECT EXISTS(SELECT 1 FROM pg_namespace "
                        "WHERE nspname NOT IN ('public','information_schema') "
                        "AND nspname NOT LIKE 'pg\\_%') "
                        "OR EXISTS(SELECT 1 FROM pg_class "
                        "WHERE relnamespace='public'::regnamespace) "
                        "OR EXISTS(SELECT 1 FROM pg_proc "
                        "WHERE pronamespace='public'::regnamespace) "
                        "OR EXISTS(SELECT 1 FROM pg_type "
                        "WHERE typnamespace='public'::regnamespace) "
                        "AS occupied"
                    )
                    contents = await cursor.fetchone()
                    if database["marker"] is not None or not contents or contents["occupied"]:
                        raise MetadataSchemaMismatch()
                    cursor = await conn.execute(
                        "SELECT EXISTS(SELECT 1 FROM pg_database d CROSS JOIN LATERAL "
                        "aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) a "
                        "WHERE d.datname=current_database() AND a.grantee NOT IN (0, d.datdba)) "
                        "AS foreign_grant"
                    )
                    grants = await cursor.fetchone()
                    if not grants or grants["foreign_grant"]:
                        raise MetadataSchemaMismatch()
                    for role in ALL_ROLES:
                        password = credentials.passwords.get(role)
                        definition = sql.SQL(
                            "CREATE ROLE {} {} NOSUPERUSER NOCREATEDB NOCREATEROLE {} NOBYPASSRLS"
                        ).format(
                            sql.Identifier(role), sql.SQL("LOGIN" if password else "NOLOGIN"),
                            sql.SQL(
                                "REPLICATION" if role == "nova_meta_backup" else "NOREPLICATION"
                            ),
                        )
                        if password:
                            hashed = conn.pgconn.encrypt_password(
                                password.get_secret_value().encode(), role.encode(),
                                b"scram-sha-256",
                            ).decode()
                            definition += sql.SQL(" PASSWORD {}").format(sql.Literal(hashed))
                        await conn.execute(definition)
                        await conn.execute(sql.SQL("COMMENT ON ROLE {} IS {}").format(
                            sql.Identifier(role), sql.Literal(marker),
                        ))
                    await conn.execute(
                        "REVOKE ALL ON DATABASE nova_control FROM PUBLIC; "
                        "REVOKE ALL ON SCHEMA public FROM PUBLIC; "
                        "GRANT CREATE ON DATABASE nova_control TO nova_meta_owner; "
                        "GRANT nova_meta_owner TO nova_meta_migrator "
                        "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
                    )
                    for role in LOGIN_ROLES:
                        await conn.execute(sql.SQL(
                            "GRANT CONNECT ON DATABASE nova_control TO {}"
                        ).format(sql.Identifier(role)))
                    for role in WORKLOAD_ROLES:
                        await conn.execute(sql.SQL(
                            "GRANT nova_meta_app TO {} WITH ADMIN FALSE, INHERIT TRUE, SET FALSE"
                        ).format(sql.Identifier(role)))
                    await conn.execute(sql.SQL("COMMENT ON DATABASE nova_control IS {}").format(
                        sql.Literal(marker),
                    ))
                    created = True
                elif database["marker"] != marker:
                    raise MetadataSchemaMismatch()
                await _verify_roles(conn, marker)
        # Fresh roles become visible only after commit. A failed login leaves a
        # recognizable deployment to verify, never a reason to rotate passwords.
        for role, password in credentials.passwords.items():
            try:
                async with await psycopg.AsyncConnection.connect(
                    make_conninfo(config.dsn, user=role, password=password.get_secret_value()),
                    autocommit=True, connect_timeout=max(1, int(config.acquire_timeout)),
                    application_name="nova-metadata-bootstrap-verification",
                ) as login:
                    if not login.pgconn.used_password or login.info.user != role:
                        raise BootstrapVerificationFailed()
            except psycopg.Error:
                raise BootstrapVerificationFailed() from None
        return BootstrapResult(created, len(LOGIN_ROLES))
    except (psycopg.errors.QueryCanceled, psycopg.errors.LockNotAvailable):
        raise MetadataTimeout() from None
    except (psycopg.errors.InvalidPassword, psycopg.errors.InvalidAuthorizationSpecification):
        raise MetadataPermissionDenied() from None
    except psycopg.OperationalError:
        raise MetadataOutcomeUnknown() from None
    except psycopg.Error:
        raise MetadataOperationFailed() from None


def main() -> int:
    from pydantic import ValidationError

    from app.core.config import MetadataBootstrapSettings

    try:
        settings = MetadataBootstrapSettings()
        result = asyncio.run(bootstrap(
            MetadataPoolConfig(
                settings.dsn.get_secret_value(), statement_timeout_ms=settings.statement_timeout_ms,
                lock_timeout_ms=settings.lock_timeout_ms,
            ),
            BootstrapCredentials.from_json(settings.role_passwords),
            deployment_id=settings.deployment_id,
        ))
    except (ValidationError, ValueError):
        print(json.dumps({"status": "REFUSED", "error": "BootstrapConfigurationError"}))
        return 1
    except (
        MetadataSchemaMismatch, MetadataTimeout, MetadataOutcomeUnknown, MetadataOperationFailed,
        MetadataPermissionDenied,
        BootstrapVerificationFailed,
    ) as exc:
        print(json.dumps({"status": "REFUSED", "error": type(exc).__name__}))
        return 1
    print(json.dumps({
        "status": "IDENTITIES_VERIFIED", "created": result.created,
        "login_count": result.login_count, "metadata_epoch_published": False,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
