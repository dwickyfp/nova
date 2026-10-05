"""Provision the News demonstration fixture on the local governed stack.

Creates the four desks of the fictional retailer (sales, workforce and operating
expenses in ``news_demo``, service reliability in ``news_engineering``), the
roles and users of the access matrix, their Ranger grants and row scopes, and
the scheduled execution account for the publishing role. The Semantic Views, the
News switches and the per-user entitlements are then set through the API by
``verify_news_demo.py``, the same way an administrator would.

    uv run python scripts/seed_news_demo.py [--last-day YYYY-MM-DD]
"""

# ruff: noqa: E402 - standalone script locates the backend package before imports

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.common.audit import write_audit_log
from app.core.database import db
from app.integrations.ranger.client import ranger_client
from app.modules.access_control.security_context import SecurityContext
from app.modules.access_control.service import access_control_service
from app.modules.users.service import user_service
from scripts.provision_task_schedule_role import bind
from tests.benchmark.news import dataset, domains

OWNER = "nova_admin"
DATABASE = "news_demo"
TABLE = "retail_sales"
EDITOR_ROLE = "news_editor"
READER_ROLE = "news_reader"
OUTSIDER_ROLE = "news_outsider"
SERVICE_ACCOUNT = "nova_task_service_news"
#: user -> (role, city scope). ``None`` means the role carries no row scope.
USERS: dict[str, tuple[str, str | None]] = {
    "news_manager": (EDITOR_ROLE, None),
    "news_off": (EDITOR_ROLE, None),
    "news_bandung": (READER_ROLE, "Bandung"),
    "news_jakarta": (READER_ROLE, "Jakarta"),
    "news_outsider": (OUTSIDER_ROLE, None),
}
CREDENTIAL_PATH = Path(os.environ.get("NEWS_DEMO_CREDENTIAL_FILE", "/tmp/nova-news-demo.env"))
BATCH = 1000


def credentials() -> dict[str, str]:
    if CREDENTIAL_PATH.exists():
        values = dict(
            line.split("=", 1)
            for line in CREDENTIAL_PATH.read_text().splitlines()
            if "=" in line
        )
        if all(name in values for name in USERS):
            return values
        raise RuntimeError("Credential file is incomplete")
    values = {name: secrets.token_urlsafe(30) for name in USERS}
    fd = os.open(CREDENTIAL_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        for name, password in values.items():
            output.write(f"{name}={password}\n")
    return values


async def audit(action: str, object_type: str, object_name: str) -> None:
    await write_audit_log(
        event_type="NEWS_DEMO", user_name=OWNER, action=action,
        object_type=object_type, object_name=object_name, status="SUCCESS",
        active_role="ACCOUNTADMIN", decision="ALLOW",
    )


async def load_domain(domain: domains.Domain, last_day: date) -> int:
    """Create and fill one extra desk's table from its deterministic generator."""
    rows = domains.generate(domain, last_day=last_day)
    target = f"{domain.database}.{domain.table}"
    columns = [f"{domain.date_column} DATE NOT NULL"]
    columns += [f"{name} VARCHAR(64) NOT NULL" for name in domain.dimensions]
    columns += [
        f"{item.column} {'BIGINT' if item.integer else 'DECIMAL(18, 2)'} NOT NULL"
        for item in domain.measures
    ]
    keys = ", ".join([domain.date_column, *domain.dimensions])
    await db.execute_system(f"CREATE DATABASE IF NOT EXISTS {domain.database}")
    await db.execute_system(
        f"CREATE TABLE IF NOT EXISTS {target} ({', '.join(columns)}) "
        f"PRIMARY KEY({keys}) DISTRIBUTED BY HASH({domain.date_column}) BUCKETS 1 "
        'PROPERTIES("replication_num"="1", "enable_persistent_index"="true")'
    )
    await db.execute_system(f"TRUNCATE TABLE {target}")
    marks = "(" + ",".join(["%s"] * len(domain.columns)) + ")"
    for start in range(0, len(rows), BATCH):
        chunk = rows[start : start + BATCH]
        await db.execute_system(
            f"INSERT INTO {target} VALUES " + ",".join([marks] * len(chunk)),
            [value for row in chunk for value in row],
        )
    await audit("SEED", "TABLE", target)
    return len(rows)


async def load(last_day: date) -> int:
    rows = dataset.generate(last_day=last_day)
    await db.execute_system(f"CREATE DATABASE IF NOT EXISTS {DATABASE}")
    await db.execute_system(
        f"CREATE TABLE IF NOT EXISTS {DATABASE}.{TABLE} ("
        "sale_date DATE NOT NULL, city VARCHAR(64) NOT NULL, channel VARCHAR(32) NOT NULL, "
        "category VARCHAR(32) NOT NULL, revenue DECIMAL(18, 2) NOT NULL, orders BIGINT NOT NULL) "
        "PRIMARY KEY(sale_date, city, channel, category) "
        "DISTRIBUTED BY HASH(sale_date) BUCKETS 1 "
        'PROPERTIES("replication_num"="1", "enable_persistent_index"="true")'
    )
    await db.execute_system(f"TRUNCATE TABLE {DATABASE}.{TABLE}")
    for start in range(0, len(rows), BATCH):
        chunk = rows[start : start + BATCH]
        await db.execute_system(
            f"INSERT INTO {DATABASE}.{TABLE} VALUES "
            + ",".join(["(%s,%s,%s,%s,%s,%s)"] * len(chunk)),
            [value for row in chunk for value in row],
        )
    await audit("SEED", "TABLE", f"{DATABASE}.{TABLE}")
    return len(rows)


async def seed(last_day: date) -> None:
    await db.init_system_pool()
    try:
        admin = SecurityContext(principal=OWNER, active_role="ACCOUNTADMIN")
        loaded = await load(last_day)
        for domain in domains.DOMAINS:
            loaded += await load_domain(domain, last_day)
        for role, purpose in (
            (EDITOR_ROLE, "Reads all News demo sales and publishes the edition"),
            (READER_ROLE, "Reads News demo sales for an assigned city"),
            (OUTSIDER_ROLE, "Has no access to the News demo sales"),
        ):
            if not await ranger_client.get_role(role):
                await access_control_service.create_role(admin, role, purpose)

        existing = {row["username"] for row in await user_service.list_users()}
        if any(name in existing for name in USERS) and not CREDENTIAL_PATH.exists():
            raise RuntimeError("Demo users exist without a matching credential file")
        passwords = credentials()
        for name, (role, _city) in USERS.items():
            if name not in existing:
                # A governed account needs exactly one explicit default role at creation.
                await user_service.create_user(
                    name,
                    passwords[name],
                    granted_roles=[role],
                    default_role_mode="explicit",
                    default_roles=[role],
                )
                await audit("CREATE_USER", "USER", name)
            await access_control_service.assign_role(admin, role=role, username=name)
            await user_service.set_default_roles(name, "%", "explicit", [role])

        # Ranger accepts one access policy per resource, so the two roles are
        # granted at different levels: the table, and every table of the database.
        for role, resource in ((EDITOR_ROLE, TABLE), (READER_ROLE, "*")):
            await access_control_service.grant_access(
                admin, role=role, catalog="default_catalog", database=DATABASE,
                table=resource, accesses=["select"],
            )
        # The publishing role reads every desk; each table is its own resource.
        for domain in domains.DOMAINS:
            await access_control_service.grant_access(
                admin, role=EDITOR_ROLE, catalog="default_catalog", database=domain.database,
                table=domain.table, accesses=["select"],
            )
        # A city reader keeps their city on every desk that has one. Engineering
        # lives in another database the reader role is not granted at all.
        scoped = [(DATABASE, TABLE)] + [
            (domain.database, domain.table) for domain in domains.DOMAINS if domain.scoped_by
        ]
        for name, (role, city) in USERS.items():
            if city:
                for database, table in scoped:
                    await access_control_service.put_data_scope(
                        admin, principal=name, role=role, catalog="default_catalog",
                        database=database, table=table, bindings=[("city", "city", [city])],
                    )
        # The publishing role carries no row scope, so its execution account
        # reads the whole table. Readers never inherit that visibility.
        await bind(EDITOR_ROLE, SERVICE_ACCOUNT, OWNER)
        print(f"rows={loaded}")
        print(f"last_day={last_day.isoformat()}")
        print(f"credential_file={CREDENTIAL_PATH}")
    finally:
        await db.close_system_pool()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--last-day",
        type=date.fromisoformat,
        default=datetime.now(ZoneInfo("Asia/Jakarta")).date() - timedelta(days=1),
        help="Newest day of data; defaults to yesterday in Asia/Jakarta.",
    )
    asyncio.run(seed(parser.parse_args().last_day))


if __name__ == "__main__":
    main()
