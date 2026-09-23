"""Provision the isolated city RBAC acceptance fixture on the local Ranger stack."""

# ruff: noqa: E402 - standalone script locates the backend package before imports

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.common.audit import write_audit_log
from app.core.database import db
from app.integrations.ranger.client import ranger_client
from app.modules.access_control.security_context import SecurityContext
from app.modules.access_control.service import access_control_service
from app.modules.agents.instructions import compile_agent_instructions
from app.modules.agents.repository import agent_repository
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.runtime import validate_semantic_model_ir
from app.modules.users.service import user_service

ROLE = "rbac_city_reader"
DATABASE = "rbac_city_demo"
TABLE = "city_sales"
OWNER = "nova_admin"
USERS = {"rbac_jakarta": "Jakarta", "rbac_bandung": "Bandung"}
MODEL_NAME = "rbac_city_sales"
AGENT_NAME = "City RBAC Agent"
MODEL_PATH = Path(__file__).resolve().parents[2] / "workspace/rbac_city_demo/city_sales.ossie.yaml"
CREDENTIAL_PATH = Path(os.environ.get("RBAC_DEMO_CREDENTIAL_FILE", "/tmp/nova-rbac-city-demo.env"))


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
        event_type="RBAC_DEMO", user_name=OWNER, action=action,
        object_type=object_type, object_name=object_name, status="SUCCESS",
        active_role="ACCOUNTADMIN", decision="ALLOW",
    )


async def seed() -> None:
    parsed = parse_ossie(MODEL_PATH.read_text())
    validation = validate_semantic_model_ir(SemanticModelIR.from_ossie(parsed.as_dict()))
    if not validation.valid:
        raise RuntimeError(f"Invalid semantic model: {validation.errors}")

    await db.init_system_pool()
    try:
        admin = SecurityContext(principal=OWNER, active_role="ACCOUNTADMIN")
        await db.execute_system(f"CREATE DATABASE IF NOT EXISTS {DATABASE}")
        await db.execute_system(
            f"CREATE TABLE IF NOT EXISTS {DATABASE}.{TABLE} ("
            "id BIGINT NOT NULL, city VARCHAR(64) NOT NULL, "
            "amount DECIMAL(18, 2) NOT NULL) PRIMARY KEY(id) "
            "DISTRIBUTED BY HASH(id) BUCKETS 1 "
            'PROPERTIES("replication_num"="1", "enable_persistent_index"="true")'
        )
        await db.execute_system(
            f"INSERT INTO {DATABASE}.{TABLE} VALUES "
            "(1, 'Jakarta', 100), (2, 'Bandung', 200), "
            "(3, 'Jakarta', 300), (4, 'Bandung', 400)"
        )
        await audit("SEED", "TABLE", f"{DATABASE}.{TABLE}")

        if not await ranger_client.get_role(ROLE):
            await access_control_service.create_role(admin, ROLE, "City RBAC demo reader")

        existing_users = {row["username"] for row in await user_service.list_users()}
        if any(name in existing_users for name in USERS) and not CREDENTIAL_PATH.exists():
            raise RuntimeError("Demo users exist without a matching credential file")
        passwords = credentials()
        for name in USERS:
            if name not in existing_users:
                await user_service.create_user(name, passwords[name])
                await audit("CREATE_USER", "USER", name)
            await access_control_service.assign_role(admin, role=ROLE, username=name)
            await user_service.set_default_roles(name, "%", "explicit", [ROLE])

        await access_control_service.grant_access(
            admin, role=ROLE, catalog="default_catalog", database=DATABASE,
            table=TABLE, accesses=["select"],
        )
        for name, city in USERS.items():
            await access_control_service.put_data_scope(
                admin, principal=name, role=ROLE, catalog="default_catalog",
                database=DATABASE, table=TABLE, bindings=[("city", "city", [city])],
            )

        existing_models = await agent_repository.list_semantic_models(owner_name=OWNER)
        model = next((row for row in existing_models if row["name"] == MODEL_NAME), None)
        model_fields = {
            "name": MODEL_NAME, "description": "City governed sales data",
            "database_name": DATABASE, "schema_name": None,
            "ossie_version": parsed.version, "definition": parsed.as_dict(),
        }
        if model:
            model = await agent_repository.update_semantic_model(
                model["semantic_model_id"], owner_name=OWNER, fields=model_fields,
            )
        else:
            model = await agent_repository.create_semantic_model(
                owner_name=OWNER, fields=model_fields,
            )
            await audit("CREATE", "SEMANTIC_MODEL", model["semantic_model_id"])
        assert model is not None

        response = (
            "Jawab hanya dengan hasil query yang berhasil dijalankan. "
            "Jika hasil kosong, katakan tidak ada data yang dapat diakses untuk filter itu."
        )
        orchestration = (
            "Gunakan semantic_query untuk agregasi kota dan query_execute untuk "
            "pertanyaan baris. Selalu jalankan query dengan role aktif pengguna."
        )
        configured_models = await db.execute_system(
            "SELECT m.provider_id, m.name FROM NOVA_SYSTEM.CONFIG_AI_MODELS m "
            "JOIN NOVA_SYSTEM.CONFIG_AI_PROVIDERS p ON m.provider_id = p.id "
            "WHERE m.type = 'llm' AND m.is_active = 1 AND p.is_active = 1 LIMIT 1"
        )
        if not configured_models["rows"]:
            raise RuntimeError("No active LLM model is configured for Agent Studio")
        provider_id, model_name = configured_models["rows"][0]
        agent_fields = {
            "name": AGENT_NAME, "description": "Governed city sales analyst",
            "database_name": DATABASE, "schema_name": None,
            "model_provider_id": provider_id, "model_name": model_name,
            "instructions_response": response,
            "instructions_orchestration": orchestration,
            "compiled_instructions": compile_agent_instructions(
                response=response, orchestration=orchestration,
                description="Governed city sales analyst", response_style=None,
            ).as_dict(),
            "sample_questions": ["Berapa total penjualan kota saya?"],
            "default_tools": ["semantic_query", "query_execute"],
            "policy": "auto_read_only", "visibility": "shared",
            "semantic_model_id": model["semantic_model_id"],
            "semantic_model_ids": [model["semantic_model_id"]],
        }
        existing_agents = await agent_repository.list_agents(owner_name=OWNER)
        agent = next((row for row in existing_agents if row["name"] == AGENT_NAME), None)
        if agent:
            agent = await agent_repository.update_agent(
                agent["agent_id"], owner_name=OWNER, fields=agent_fields,
            )
        else:
            agent = await agent_repository.create_agent(owner_name=OWNER, fields=agent_fields)
            await audit("CREATE", "AGENT", agent["agent_id"])
        assert agent is not None
        await agent_repository.add_agent_role(
            agent["agent_id"], owner_name=OWNER, role_name=ROLE,
        )
        await audit("GRANT_USAGE", "AGENT", agent["agent_id"])
        print(f"agent_id={agent['agent_id']}")
        print(f"semantic_model_id={model['semantic_model_id']}")
        print(f"credential_file={CREDENTIAL_PATH}")
    finally:
        await db.close_system_pool()


if __name__ == "__main__":
    asyncio.run(seed())
