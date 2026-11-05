"""Governed acceptance for Nova Catalog phase 2: a Postgres foreign catalog.

Proves the phase 2 exit criterion on the patched FE and Ranger of the running
``docker`` stack:

* ``CREATE CONNECTION`` / ``CREATE FOREIGN CATALOG`` / ``REFRESH CATALOG`` and a
  catalog-qualified ``GRANT`` run through Nova's SQL path as ``nova_admin``
  (``ACCOUNTADMIN``), with the connection's credentials entered through the
  write-only credential flow;
* a non-root user whose active role has the grant can ``SELECT`` from
  ``<catalog>.public.orders``, and Ranger's row filter and mask apply;
* the same user under a role without the grant, a table without a grant, and
  the role after ``REVOKE`` are all refused;
* no credential appears in ``SHOW CREATE CATALOG``, a ``CATALOG_*`` row, or an
  ``AUDIT_LOG`` row written by the run.

The source database must already hold ``public.orders`` (id, city, amount,
customer_email) and ``public.customers``; ``docker/catalog-test/`` seeds them.

Environment (nothing here is printed):

* ``NOVA_ADMIN_TEST_PASSWORD``: ``nova_admin``'s password (``docker/.env``).
* ``NOVA_CATALOG_E2E_PG_ENDPOINT``: the source as the FE and BE reach it,
  ``host:port/database``.
* ``NOVA_CATALOG_E2E_PG_USER`` / ``NOVA_CATALOG_E2E_PG_PASSWORD``: a read-only
  account on the source.

Alice and Bob are the local acceptance principals from ``docker/ranger``; their
``nova_scope.marketing.city`` attributes (Jakarta, Bandung) drive the filter.
Every object the run creates is removed at the end, pass or fail.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

CATALOG = "pg_catalog_x"
CONNECTION = "pg_catalog_x_conn"
ROLE = "marketing"
#: Fixture passwords of the local acceptance principals (scripts/verify_ranger_e2e.py).
ALICE = ("alice", "NovaAlice2026!")
BOB = ("bob", "NovaBob2026!")
#: How long FE policy propagation may take before a check fails.
PROPAGATION_SECONDS = 120


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} is not set")
    return value


async def _sql(user: str, password: str, role: str, sql: str) -> Any:
    from app.core.security import encrypt_password
    from app.modules.query.service import query_service

    return await query_service.execute(
        sql=sql,
        username=user,
        encrypted_password=encrypt_password(password),
        role=role,
        confirm_destructive=True,
    )


async def _until(description: str, check) -> Any:
    """Retry ``check`` until it passes: Ranger policy downloads are periodic."""
    deadline = time.monotonic() + PROPAGATION_SECONDS
    last: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            result = await check()
            print(f"OK   {description}")
            return result
        except AssertionError as exc:
            last = exc
            await asyncio.sleep(5)
    raise AssertionError(f"{description}: {last}")


def _no_secret(value: object, secret: str, where: str) -> None:
    assert secret not in str(value), f"credential found in {where}"


async def main() -> None:
    from app.common.crypto import EncryptionError  # noqa: F401 - fail early without FERNET_KEY
    from app.core.database import db
    from app.integrations.ranger.client import RangerClient
    from app.integrations.ranger.compiler import compile_mask_policy, compile_row_filter_policy
    from app.modules.access_control.security_context import require_security_context
    from app.modules.catalog.connections import connection_service
    from app.modules.catalog.service import catalog_service

    await db.init_system_pool()
    admin = ("nova_admin", _env("NOVA_ADMIN_TEST_PASSWORD"))
    endpoint = _env("NOVA_CATALOG_E2E_PG_ENDPOINT")
    source_user = _env("NOVA_CATALOG_E2E_PG_USER")
    source_password = _env("NOVA_CATALOG_E2E_PG_PASSWORD")
    started = time.strftime("%Y-%m-%d %H:%M:%S")

    from app.core.security import encrypt_password

    admin_user = {
        "username": admin[0],
        "encrypted_password": encrypt_password(admin[1]),
        "active_role": "ACCOUNTADMIN",
        "security": require_security_context(principal=admin[0], active_role="ACCOUNTADMIN"),
    }
    ranger = RangerClient()
    policies = [
        compile_row_filter_policy(
            role=ROLE, catalog=CATALOG, database="public", table="orders",
            bindings=[("city", "city", ["Jakarta"])],
        ),
        compile_mask_policy(
            role=ROLE, catalog=CATALOG, database="public", table="orders",
            column="customer_email", mask_type="MASK",
        ),
    ]

    async def admin_sql(sql: str) -> Any:
        result = await _sql(*admin, "ACCOUNTADMIN", sql)
        assert result.error is None, f"{sql.split()[0:3]}: {result.error}"
        return result

    try:
        # 1. Connection (SQL), credentials (write-only entry), catalog, refresh.
        await admin_sql(
            f"CREATE CONNECTION {CONNECTION} TYPE = postgres ENDPOINT = '{endpoint}' "
            f"SECRET = 'nova://{CONNECTION}' COMMENT = 'Phase 2 acceptance'"
        )
        await connection_service.set_credentials(
            CONNECTION, source_user, source_password, admin_user
        )
        await admin_sql(f"CREATE FOREIGN CATALOG {CATALOG} USING CONNECTION {CONNECTION}")
        refreshed = await admin_sql(f"REFRESH CATALOG {CATALOG}")
        assert refreshed.rows and refreshed.rows[0][1] == "ok", refreshed.rows
        print("OK   connection, foreign catalog, and refresh through Nova SQL")

        shown = await admin_sql("SHOW CONNECTIONS")
        assert any(row[0] == CONNECTION and CATALOG in (row[3] or "") for row in shown.rows)
        _no_secret(shown.rows, endpoint.split(":")[0], "SHOW CONNECTIONS")

        # 2. Grant through SQL, filter and mask through the compiler.
        await admin_sql(f"GRANT SELECT ON TABLE {CATALOG}.public.orders TO ROLE {ROLE}")
        for policy in policies:
            await ranger.put_policy(policy)
        print("OK   catalog-qualified GRANT and Ranger filter/mask policies written")

        orders = f"SELECT id, city, customer_email FROM {CATALOG}.public.orders ORDER BY id"

        async def alice_sees_jakarta_masked():
            result = await _sql(*ALICE, ROLE, orders)
            assert result.error is None, result.error
            cities = {row[1] for row in result.rows}
            assert cities == {"Jakarta"}, cities
            assert all("@" not in str(row[2]) for row in result.rows), result.rows
            return result

        await _until("alice/marketing: only Jakarta rows, email masked", alice_sees_jakarta_masked)

        async def bob_sees_bandung():
            result = await _sql(*BOB, ROLE, orders)
            assert result.error is None, result.error
            assert {row[1] for row in result.rows} == {"Bandung"}, result.rows

        await _until("bob/marketing: only Bandung rows", bob_sees_bandung)

        async def refused(user, role, sql):
            result = await _sql(*user, role, sql)
            assert result.error, f"expected a refusal, got {len(result.rows or [])} rows"

        await _until(
            "alice/finance (no grant): refused",
            lambda: refused(ALICE, "finance", orders),
        )
        await _until(
            "alice/marketing on customers (no grant): refused",
            lambda: refused(ALICE, ROLE, f"SELECT * FROM {CATALOG}.public.customers"),
        )

        # 3. Nothing leaked a credential.
        detail = await catalog_service.get_catalog(CATALOG, admin_user)
        assert detail is not None and detail.create_statement
        _no_secret(detail.model_dump_json(), source_password, "SHOW CREATE CATALOG / detail")
        for table in ("CATALOG_CATALOGS", "CATALOG_CONNECTIONS", "CATALOG_SYNC_RUNS"):
            rows = await db.execute_system(f"SELECT * FROM NOVA_SYSTEM.{table}")
            _no_secret(rows, source_password, table)
        audit = await db.execute_system(
            "SELECT * FROM NOVA_SYSTEM.AUDIT_LOG WHERE event_time >= %s", [started]
        )
        _no_secret(audit, source_password, "AUDIT_LOG")
        print("OK   no credential in catalog detail, CATALOG_* rows, or AUDIT_LOG")

        # 4. Revoke closes it again.
        await admin_sql(f"REVOKE SELECT ON TABLE {CATALOG}.public.orders FROM ROLE {ROLE}")
        await _until(
            "alice/marketing after REVOKE: refused", lambda: refused(ALICE, ROLE, orders)
        )
    finally:
        for policy in policies:
            existing = await ranger.get_policy(policy.name)
            if existing and existing.get("id") is not None:
                await ranger.delete_policy(int(existing["id"]))
        await _sql(*admin, "ACCOUNTADMIN",
                   f"REVOKE SELECT ON TABLE {CATALOG}.public.orders FROM ROLE {ROLE}")
        try:
            await catalog_service.drop(CATALOG, admin_user)
        except Exception as exc:  # noqa: BLE001 - cleanup reports and continues
            print(f"cleanup: catalog not dropped ({type(exc).__name__})")
        await connection_service.drop(CONNECTION, admin_user, if_exists=True)
    print("Nova Catalog phase 2 acceptance passed")


if __name__ == "__main__":
    asyncio.run(main())
