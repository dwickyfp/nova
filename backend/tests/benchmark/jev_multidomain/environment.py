"""Real Nova fixtures with isolated journal tables and existing authenticated access."""

from __future__ import annotations

import hashlib
import json
import subprocess
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from unittest.mock import patch

import yaml

from tests.benchmark.jev_multidomain.cases import frozen_document
from tests.benchmark.jev_multidomain.catalog import DESCRIPTIONS, definition, manifest
from tests.benchmark.jev_multidomain.data import DATABASE, SCHEMAS, SEED, VERSION, generate

ARTIFACTS = Path(__file__).resolve().parents[4] / "docs/benchmarks/jev-multidomain-20260925"
JOURNALS = {
    f"NOVA_SYSTEM.CONFIG_{name}": f"NOVA_SYSTEM.CONFIG_JEVBENCH_{name}"
    for name in [
        "AGENT_RUNS",
        "AGENT_MESSAGES",
        "AGENT_SESSION_EVENTS",
        "ASSISTANT_THREADS",
        "ASSISTANT_MESSAGES",
    ]
}


def read_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines(keepends=True)
        if line.endswith("\n")
    ]


def public_trace(value):
    if isinstance(value, dict):
        return {
            key: "[redacted]"
            if key in {"session_id", "encrypted_password", "api_key", "authorization", "password"}
            else public_trace(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [public_trace(item) for item in value]
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            nested = json.loads(value)
        except ValueError:
            return value
        redacted = public_trace(nested)
        if redacted != nested:
            return json.dumps(redacted, ensure_ascii=False)
    return value


def configure_local_services() -> None:
    from app.core.config import settings

    if not settings.RANGER_PASSWORD:
        raw = subprocess.check_output(
            ["docker", "inspect", "nova-ranger-policy-proxy", "--format", "{{json .Config.Env}}"],
            text=True,
        )
        values = dict(item.split("=", 1) for item in json.loads(raw) if "=" in item)
        settings.RANGER_USERNAME = values["RANGER_USERNAME"]
        settings.RANGER_PASSWORD = values["RANGER_PASSWORD"]


async def authenticated_user() -> dict:
    from app.core.redis import SESSION_PREFIX, session_store

    if session_store._redis is None:
        await session_store.init()
    async for key in session_store._redis.scan_iter(match=SESSION_PREFIX + "*"):
        session = await session_store.get(key.removeprefix(SESSION_PREFIX))
        if (
            session
            and session.get("username") == "nova_admin"
            and session.get("active_role") == "ACCOUNTADMIN"
        ):
            # Mirror get_current_user's activity refresh when invoking the worker directly.
            await session_store.refresh(key.removeprefix(SESSION_PREFIX))
            return {**session, "session_id": key.removeprefix(SESSION_PREFIX)}
    raise RuntimeError("An authenticated nova_admin ACCOUNTADMIN session is required")


@contextmanager
def isolated_agent_registry(agent_ids: set[str]):
    from app.modules.agents.repository import agent_repository

    own = agent_repository.list_agents
    shared = agent_repository.list_shared_agents

    async def own_candidates(*args, **kwargs):
        return [row for row in await own(*args, **kwargs) if row["agent_id"] in agent_ids]

    async def shared_candidates(*args, **kwargs):
        return [row for row in await shared(*args, **kwargs) if row["agent_id"] in agent_ids]

    with (
        patch.object(agent_repository, "list_agents", own_candidates),
        patch.object(agent_repository, "list_shared_agents", shared_candidates),
    ):
        yield


@contextmanager
def isolated_journals():
    from app.core.database import db

    system_conn = db.system_conn

    def rewrite(sql):
        for original, replacement in JOURNALS.items():
            sql = sql.replace(original, replacement)
        return sql

    class Cursor:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        async def __aenter__(self):
            self.entered = await self.wrapped.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self.wrapped.__aexit__(*args)

        async def execute(self, sql, *args, **kwargs):
            return await self.entered.execute(rewrite(sql), *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.entered, name)

    class Connection:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def cursor(self, *args, **kwargs):
            return Cursor(self.wrapped.cursor(*args, **kwargs))

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

    @asynccontextmanager
    async def isolated_connection():
        async with system_conn() as connection:
            yield Connection(connection)

    with patch.object(db, "system_conn", isolated_connection):
        yield


async def prepare() -> dict:
    from app.common.audit import write_audit_log
    from app.core.database import db
    from app.modules.agents.access import access_fingerprint, has_verified_access, verify_access
    from app.modules.agents.capabilities import CapabilityManifest, capability_repository
    from app.modules.agents.repository import agent_repository
    from app.modules.agents.router import create_agent
    from app.modules.agents.schemas import AgentCreateRequest
    from app.modules.ai_ml.decision_settings import read_decision_settings
    from app.modules.ai_ml.service import ai_service
    from app.modules.intelligence.semantic_views import SemanticViewCreate, semantic_view_service

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    frozen = frozen_document()
    ground_truth = ARTIFACTS / "ground_truth.json"
    if ground_truth.exists() and json.loads(ground_truth.read_text())["sha256"] != frozen["sha256"]:
        raise RuntimeError("Frozen ground truth has changed; create a new benchmark version")
    ground_truth.write_text(json.dumps(frozen, ensure_ascii=False, indent=2))
    user = await authenticated_user()
    config = await read_decision_settings()
    heavy = await ai_service.get_model(config.heavy_model_id)
    await db.execute_system(f"CREATE DATABASE IF NOT EXISTS {DATABASE}")
    rows = generate()
    for table, schema in SCHEMAS.items():
        columns = [part.split()[0] for part in schema.split(", ")]
        key = columns[0]
        definitions = []
        for index, part in enumerate(schema.split(", ")):
            name, kind = part.split(" ", 1)
            definitions.append(f"`{name}` {kind}" + (" NOT NULL" if index == 0 else ""))
        await db.execute_system(
            f"CREATE TABLE IF NOT EXISTS {DATABASE}.{table} ({', '.join(definitions)}) "
            f"PRIMARY KEY (`{key}`) DISTRIBUTED BY HASH (`{key}`) BUCKETS 1 "
            'PROPERTIES("replication_num"="1", "enable_persistent_index"="true")'
        )
        count = await db.execute_system(f"SELECT COUNT(*) FROM {DATABASE}.{table}")
        if count["rows"][0][0] != len(rows[table]):
            for start in range(0, len(rows[table]), 1000):
                chunk = rows[table][start : start + 1000]
                placeholder = "(" + ",".join(["%s"] * len(columns)) + ")"
                await db.execute_system(
                    f"INSERT INTO {DATABASE}.{table} "
                    f"({','.join('`' + c + '`' for c in columns)}) VALUES "
                    + ",".join([placeholder] * len(chunk)),
                    [value for row in chunk for value in row],
                )
        print(
            json.dumps({"stage": "dataset", "table": table, "rows": len(rows[table])}), flush=True
        )
        await write_audit_log(
            event_type="JEV_BENCHMARK",
            user_name=user["username"],
            action="PREPARE",
            object_type="TABLE",
            object_name=f"{DATABASE}.{table}",
            status="SUCCESS",
            active_role=user["active_role"],
        )
    for original, copy in JOURNALS.items():
        await db.execute_system(f"CREATE TABLE IF NOT EXISTS {copy} LIKE {original}")
    path = ARTIFACTS / "environment.json"
    stored = json.loads(path.read_text()) if path.exists() else {}
    result = {
        "version": VERSION,
        "seed": SEED,
        "database": DATABASE,
        "ground_truth_sha256": frozen["sha256"],
        "owner": user["username"],
        "row_counts": {name: len(value) for name, value in rows.items()},
        "decision_settings": config.model_dump(),
        "answer_model": heavy["name"],
        "answer_provider_id": heavy["provider_id"],
        "agents": dict(stored.get("agents") or {}),
        "views": dict(stored.get("views") or {}),
        "data_sha256": hashlib.sha256(
            json.dumps(rows, default=str, sort_keys=True).encode()
        ).hexdigest(),
        "journal_tables": JOURNALS,
    }
    for domain in ["FINANCE", "MARKETING", "SALES"]:
        views = []
        if domain != "SALES":
            source = yaml.safe_dump(definition(domain), sort_keys=False, allow_unicode=True)
            (ARTIFACTS / f"{domain.lower()}.ossie.yaml").write_text(source)
            view = (stored.get("views") or {}).get(domain)
            if view is None:
                created = await semantic_view_service.create(
                    SemanticViewCreate(
                        name=f"jev_bench_{domain.lower()}",
                        database=DATABASE,
                        schema_name="benchmark",
                        definition=source,
                    ),
                    user,
                )
                view = {"id": created["id"], "version": 1}
                result["views"][domain] = view
                path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            report = await semantic_view_service.validate(view["id"], view["version"], user)
            if not report["valid"]:
                (ARTIFACTS / f"{domain.lower()}-validation.json").write_text(
                    json.dumps(report, default=str, indent=2)
                )
                raise RuntimeError(f"{domain} Semantic View validation failed")
            await semantic_view_service.publish(view["id"], view["version"], user)
            view["source_sha256"] = hashlib.sha256(source.encode()).hexdigest()
            result["views"][domain] = view
            views = [view["id"]]
        agent = (stored.get("agents") or {}).get(domain)
        if agent is None:
            request = AgentCreateRequest(
                name=f"JEV Benchmark {domain.title()}",
                description=DESCRIPTIONS[domain],
                database_name=DATABASE,
                model_provider_id=heavy["provider_id"],
                model_name=heavy["name"],
                instructions_orchestration=DESCRIPTIONS[domain]
                + " Use the bound Semantic View for authoritative metrics. Consult "
                "another specialist "
                "when the request needs that specialist's evidence. Preserve units, period and "
                "grouping. Ask for clarification if incompatible business "
                "definitions remain unresolved.",
                instructions_response="Answer in the user's language with numbers, units, "
                "period and "
                "sources. State missing evidence. These are synthetic business "
                "records. Do not claim "
                "causality from correlation or substitute attributed sales for recognized revenue.",
                default_tools=["semantic_query"] if views else [],
                semantic_view_ids=views,
                budget_seconds=120,
                budget_tokens=16000,
            )
            created = await create_agent(request, user=user)
            agent = {"id": created.agent_id}
            result["agents"][domain] = agent
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        row = await agent_repository.get_agent(agent["id"], owner_name=user["username"])
        cap = manifest(domain)
        await capability_repository.put(row, CapabilityManifest.model_validate(cap))
        await agent_repository.add_agent_role(
            agent["id"], owner_name=user["username"], role_name=user["active_role"]
        )
        items = await verify_access(
            agent=row,
            role_name=user["active_role"],
            username=user["username"],
            encrypted_password=user["encrypted_password"],
            session_id=user["session_id"],
        )
        if not all(item.granted for item in items):
            raise RuntimeError(f"{domain} agent dependencies are not authorized")
        await agent_repository.set_agent_role_verification(
            agent["id"],
            owner_name=user["username"],
            role_name=user["active_role"],
            fingerprint=await access_fingerprint(row),
        )
        if not await has_verified_access(row, role_name=user["active_role"], user=user):
            raise RuntimeError(f"{domain} agent access verification did not persist")
        agent["manifest"] = cap
        agent["instruction_sha256"] = hashlib.sha256(
            json.dumps(
                {
                    key: row.get(key)
                    for key in ["instructions_response", "instructions_orchestration"]
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        result["agents"][domain] = agent
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({"stage": "agent", "domain": domain, "verified": True}), flush=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


async def update_views() -> dict:
    """Publish a documented fixture revision; preserve original metadata artifacts."""
    from app.modules.agents.access import access_fingerprint, verify_access
    from app.modules.agents.repository import agent_repository
    from app.modules.intelligence.semantic_views import (
        SemanticViewVersionCreate,
        semantic_view_service,
    )

    path = ARTIFACTS / "environment.json"
    result = json.loads(path.read_text())
    original = ARTIFACTS / "environment-v1.json"
    if not original.exists():
        original.write_text(path.read_text())
    user = await authenticated_user()
    for domain in ["FINANCE", "MARKETING"]:
        view = result["views"][domain]
        source_path = ARTIFACTS / f"{domain.lower()}.ossie.yaml"
        archive = ARTIFACTS / f"{domain.lower()}-v1.ossie.yaml"
        if not archive.exists():
            archive.write_text(source_path.read_text())
        source = yaml.safe_dump(definition(domain), sort_keys=False, allow_unicode=True)
        fingerprint = hashlib.sha256(source.encode()).hexdigest()
        if fingerprint != view["source_sha256"]:
            version = await semantic_view_service.add_version(
                view["id"], SemanticViewVersionCreate(definition=source), user
            )
            report = await semantic_view_service.validate(view["id"], version["version"], user)
            if not report["valid"]:
                raise RuntimeError(f"Revised {domain} metadata did not validate")
            await semantic_view_service.publish(view["id"], version["version"], user)
            view.update(version=version["version"], source_sha256=fingerprint)
            source_path.write_text(source)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        row = await agent_repository.get_agent(
            result["agents"][domain]["id"], owner_name=user["username"]
        )
        checks = await verify_access(
            agent=row,
            role_name=user["active_role"],
            username=user["username"],
            encrypted_password=user["encrypted_password"],
            session_id=user["session_id"],
        )
        if not all(check.granted for check in checks):
            raise RuntimeError("Revised metadata access verification failed")
        await agent_repository.set_agent_role_verification(
            row["agent_id"],
            owner_name=user["username"],
            role_name=user["active_role"],
            fingerprint=await access_fingerprint(row),
        )
    return {domain: view["version"] for domain, view in result["views"].items()}
