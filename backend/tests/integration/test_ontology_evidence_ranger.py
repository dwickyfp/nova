"""Acceptance: join-discovery profiles obey the caller's Ranger row filters.

Two principals share one role; a Ranger row filter shows each a different city.
Profiles, sketches and exact inclusion counts are computed through the same
execution path a join run uses (``EvaluationService._execute`` → ``query_service``
with the caller's session and role), and must reflect only the rows each may
see. The users, role, database and policies are unique to the run and removed
afterwards.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from uuid import uuid4

import httpx
import pytest

from app.common.nova_system import init_nova_system
from app.core.config import settings
from app.core.database import db
from app.core.redis import session_store
from app.core.security import decode_token
from app.integrations.ranger.client import RangerClient
from app.integrations.ranger.schemas import RangerPolicy
from app.main import create_app
from app.modules.access_control.security_context import SecurityContext
from app.modules.access_control.service import access_control_service
from app.modules.ontology.evaluation.service import EvaluationService
from app.modules.ontology.evidence.joins import candidates
from app.modules.ontology.evidence.profiling import (
    ColumnProfile,
    Sketch,
    inclusion_sql,
    parse_table_profile,
    sketch_sql,
    table_profile_sql,
)
from tests.benchmark.business_intelligence.client import StudioClient

pytestmark = [
    pytest.mark.engine,
    pytest.mark.skipif(
        os.getenv("NOVA_ONTOLOGY_EVIDENCE_RANGER_ACCEPTANCE") != "1",
        reason="Creates users, a role and Ranger policies on a patched FE; opt in explicitly",
    ),
]

COLUMNS = [("id", "bigint"), ("city", "varchar"), ("outlet_id", "varchar")]


async def _session_user(client: StudioClient) -> dict:
    token = client.http.headers["Authorization"].removeprefix("Bearer ")
    sid = decode_token(token)["sid"]
    session = await session_store.get(sid)
    assert session is not None
    return {**session, "session_id": sid}


async def test_profiles_and_sketches_see_only_the_callers_rows():
    await db.init_system_pool()
    await session_store.init()
    await init_nova_system()
    suffix = uuid4().hex[:10]
    database, role = "evd_data_" + suffix, "evd_read_" + suffix
    principals = ("evd_a_" + suffix, "evd_b_" + suffix)
    password = secrets.token_urlsafe(24)
    operator = SecurityContext(principal="nova_admin", active_role="ACCOUNTADMIN")
    ranger, policies, clients = RangerClient(), [], []
    try:
        await access_control_service.create_role(operator, role)
        for username in principals:
            await db.execute_system(f"CREATE USER '{username}' IDENTIFIED BY %s", [password])
            await access_control_service.assign_role(operator, role=role, username=username)
            await db.execute_system(f"SET DEFAULT ROLE `{role}` TO '{username}'")
        await db.execute_system(f"CREATE DATABASE {database}")
        await db.execute_system(
            f"CREATE TABLE {database}.sales (id BIGINT, city VARCHAR(32), outlet_id VARCHAR(16)) "
            "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
            "PROPERTIES('replication_num'='1')")
        await db.execute_system(
            f"CREATE TABLE {database}.outlets (outlet_id VARCHAR(16), city VARCHAR(32)) "
            "DUPLICATE KEY(outlet_id) DISTRIBUTED BY HASH(outlet_id) BUCKETS 1 "
            "PROPERTIES('replication_num'='1')")
        # Jakarta orders go to outlets J-0..J-9, Bandung orders to B-0..B-4.
        rows = [(i, "Jakarta", f"J-{i % 10}") for i in range(60)] + [
            (100 + i, "Bandung", f"B-{i % 5}") for i in range(30)]
        await db.execute_system(
            f"INSERT INTO {database}.sales VALUES " + ",".join(["(%s,%s,%s)"] * len(rows)),
            [value for row in rows for value in row])
        outlets = [(f"J-{i}", "Jakarta") for i in range(10)] + [(f"B-{i}", "Bandung")
                                                                for i in range(5)]
        await db.execute_system(
            f"INSERT INTO {database}.outlets VALUES " + ",".join(["(%s,%s)"] * len(outlets)),
            [value for row in outlets for value in row])
        resources = {
            "catalog": {"values": ["default_catalog"]},
            "database": {"values": [database]},
            "table": {"values": ["*"]},
            "column": {"values": ["*"]},
        }
        for policy in (
            {"name": f"evd/{suffix}/access", "policyType": 0, "resources": resources,
             "policyItems": [{"roles": [role],
                              "accesses": [{"type": "select", "isAllowed": True}]}]},
            *({"name": f"evd/{suffix}/rows/{table}", "policyType": 2,
               "resources": {**{k: v for k, v in resources.items() if k != "column"},
                             "table": {"values": [table]}},
               "rowFilterPolicyItems": [
                   {"users": [username], "accesses": [{"type": "select", "isAllowed": True}],
                    "rowFilterInfo": {"filterExpr": f"city = '{city}'"}}
                   for username, city in zip(principals, ("Jakarta", "Bandung"), strict=True)
               ]} for table in ("sales", "outlets")),
        ):
            policies.append(await ranger.put_policy(RangerPolicy.model_validate(
                {"service": settings.RANGER_SERVICE_NAME, **policy})))
        application = create_app()
        for username in principals:
            client = StudioClient(httpx.AsyncClient(
                transport=httpx.ASGITransport(application), base_url="http://evidence.test",
                timeout=120))
            clients.append(client)
            await client.login(username, password, role)
        service = EvaluationService(models=lambda _user: asyncio.sleep(0, {}))
        model = {"database_name": database}

        async def profile(user: dict) -> list[ColumnProfile]:
            columns = [name for name, _ in COLUMNS]
            _names, found, error = await service._execute(
                table_profile_sql(database, "sales", columns), model, user)
            assert error is None and found
            return parse_table_profile(database, "sales", COLUMNS, found[0])

        async def filtered() -> bool:
            # Ranger syncs new policies to the FE after a delay; until then the query is
            # refused or unfiltered, so wait for the filtered count rather than assert.
            columns = [name for name, _ in COLUMNS]
            _names, found, error = await service._execute(
                table_profile_sql(database, "sales", columns), model,
                await _session_user(clients[0]))
            return error is None and bool(found) and int(found[0][0]) == 60

        async with asyncio.timeout(90):
            while not await filtered():
                await asyncio.sleep(1)

        seen = {}
        for client, city, count, outlets_seen in zip(
                clients, ("Jakarta", "Bandung"), (60, 30), (10, 5), strict=True):
            user = await _session_user(client)
            profiles = {item.column: item for item in await profile(user)}
            # Counts are those of the caller's rows, never of the whole table.
            assert profiles["id"].rows == count and profiles["outlet_id"].ndv == outlets_seen
            assert profiles["city"].ndv == 1
            _names, hashes, error = await service._execute(
                sketch_sql(profiles["outlet_id"]), model, user)
            assert error is None and len(hashes) == outlets_seen
            target = ColumnProfile(database, "outlets", "outlet_id", "varchar", outlets_seen,
                                   outlets_seen, outlets_seen, 3, 3)
            _names, included, error = await service._execute(
                inclusion_sql(profiles["outlet_id"], target), model, user)
            assert error is None and included[0] == [outlets_seen, outlets_seen]
            seen[city] = {int(row[0]) for row in hashes}
            # Each principal finds the join within what it may see.
            sketches = {
                profiles["outlet_id"].key: Sketch(frozenset(seen[city]), outlets_seen),
                target.key: Sketch(frozenset(seen[city]), outlets_seen),
            }
            found = candidates([profiles["outlet_id"], target], sketches)
            assert [(item.source, item.target) for item in found] == [
                (profiles["outlet_id"].key, target.key)]
        # The two principals' sketches share no value: neither saw the other's outlets.
        assert not seen["Jakarta"] & seen["Bandung"]
    finally:
        original_error = sys.exc_info()[0] is not None
        errors = []

        async def clean(operation):
            try:
                await operation
            except Exception as exc:  # noqa: BLE001 - every cleanup step runs
                errors.append(type(exc).__name__)

        for client in clients:
            token = client.http.headers.get("Authorization", "").removeprefix("Bearer ")
            if token:
                await clean(session_store.delete(decode_token(token)["sid"]))
            await clean(client.http.aclose())
        for policy in reversed(policies):
            await clean(ranger.delete_policy(policy["id"]))
        await clean(db.execute_system(f"DROP DATABASE IF EXISTS {database}"))
        for username in principals:
            await clean(db.execute_system(f"DROP USER IF EXISTS '{username}'"))
        await clean(access_control_service.drop_role(operator, role))
        await clean(session_store.close())
        await clean(db.close_system_pool())
        if errors and not original_error:
            pytest.fail(f"Acceptance cleanup failed: {errors}")
