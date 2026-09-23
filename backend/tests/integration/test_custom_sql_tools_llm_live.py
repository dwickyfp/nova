from __future__ import annotations

import json
import os
import secrets
from contextlib import suppress
from uuid import uuid4

import asyncmy
import pytest

from app.core.config import Settings, settings
from app.core.security import encrypt_password
from app.modules.agents.tools.custom_tool import CustomToolRunner
from app.modules.assistant.provider import AssistantProviderClient
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.conftest import engine_host_ports

pytestmark = [
    pytest.mark.engine,
    pytest.mark.skipif(
        os.getenv("NOVA_CUSTOM_SQL_LLM_LIVE") != "1"
        or not os.getenv("NOVA_CUSTOM_SQL_LLM_PORT"),
        reason=(
            "Set NOVA_CUSTOM_SQL_LLM_LIVE=1 and NOVA_CUSTOM_SQL_LLM_PORT "
            "for the live provider probe"
        ),
    ),
]


def _event(frame: str) -> tuple[str, dict]:
    event = ""
    data = {}
    for line in frame.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
    return event, data


async def test_real_provider_uses_two_temporary_custom_sql_tools(app, monkeypatch) -> None:
    import app.common.crypto as provider_crypto

    host = "127.0.0.1"
    test_port = engine_host_ports()["starrocks-fe"]
    test_fernet_key = settings.FERNET_KEY
    monkeypatch.setattr(settings, "STARROCKS_HOST", host)
    monkeypatch.setattr(
        settings, "STARROCKS_FE_MYSQL_PORT", int(os.environ["NOVA_CUSTOM_SQL_LLM_PORT"])
    )
    monkeypatch.setattr(settings, "FERNET_KEY", Settings().FERNET_KEY)
    monkeypatch.setattr(provider_crypto, "_fernet", None)
    monkeypatch.setattr(settings, "RANGER_ENABLED", False)
    try:
        provider_config = await AssistantProviderClient(
            timeout_seconds=90, max_attempts=1
        ).resolve()
    finally:
        monkeypatch.setattr(settings, "FERNET_KEY", test_fernet_key)
        monkeypatch.setattr(provider_crypto, "_fernet", None)

    class PinnedProvider(AssistantProviderClient):
        async def resolve(self, **_kwargs):
            return provider_config

    provider = PinnedProvider(timeout_seconds=90, max_attempts=1)
    monkeypatch.setattr(settings, "STARROCKS_FE_MYSQL_PORT", test_port)

    suffix = uuid4().hex[:10]
    database = f"nova_llm_custom_{suffix}"
    role = f"llm_custom_role_{suffix}"
    username = f"llm_custom_user_{suffix}"
    password = secrets.token_urlsafe(20)
    root = await asyncmy.connect(host=host, port=test_port, user="root", password="")
    created_database = False
    created_role = False
    created_user = False

    try:
        async with root.cursor() as cursor:
            await cursor.execute(f"CREATE DATABASE `{database}`")
            created_database = True
            await cursor.execute(
                f"CREATE TABLE `{database}`.`records` "
                "(id BIGINT NOT NULL, value VARCHAR(64)) "
                "PRIMARY KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
                'PROPERTIES("replication_num"="1")'
            )
            await cursor.execute(f"CREATE ROLE `{role}`")
            created_role = True
            await cursor.execute(f"CREATE USER '{username}' IDENTIFIED BY '{password}'")
            created_user = True
            await cursor.execute(f"GRANT `{role}` TO '{username}'")
            await cursor.execute(
                f"GRANT SELECT, INSERT ON ALL TABLES IN DATABASE `{database}` TO ROLE `{role}`"
            )

        registry = ToolRegistry()
        registry.register(
            CustomToolRunner(
                {
                    "name": "insert_probe",
                    "kind": "procedure",
                    "description": "Insert the supplied value into the isolated records table.",
                    "database_name": database,
                    "definition": {
                        "parameters": [
                            {"name": "value", "type": "string", "description": "Value to insert"}
                        ],
                        "statements": ["INSERT INTO records (id, value) VALUES (1, {{value}})"],
                        "output_mode": "run",
                    },
                }
            )
        )
        registry.register(
            CustomToolRunner(
                {
                    "name": "select_probe",
                    "kind": "procedure",
                    "description": "Select the inserted value from the isolated records table.",
                    "database_name": database,
                    "definition": {
                        "parameters": [
                            {"name": "value", "type": "string", "description": "Expected value"}
                        ],
                        "statements": [
                            "SELECT value FROM records WHERE id = 1 AND value = {{value}}"
                        ],
                        "output_mode": "result",
                    },
                }
            )
        )
        from app.modules.query.service import query_service

        pipeline_errors = []
        original_execute = query_service.execute_statements

        async def capture_errors(**kwargs):
            results = await original_execute(**kwargs)
            pipeline_errors.extend(
                str(result.error).replace(password, "***")
                for result in results
                if result.error
            )
            return results

        monkeypatch.setattr(query_service, "execute_statements", capture_errors)
        context = LoopContext(
            user_name=username,
            thread_id=f"llm-custom-{suffix}",
            database=database,
            agent_id=f"temporary-{suffix}",
            user={
                "username": username,
                "encrypted_password": encrypt_password(password),
                "active_role": role,
                "assigned_roles": [role],
                "security_context_version": 1,
            },
        )
        thread = AssistantThread(
            thread_id=context.thread_id,
            user_name=username,
            title="Temporary custom SQL tool probe",
        )
        thread.consent.always_allow_read_only = True
        consent_prompts = []

        async def consent(invocation, classification):
            consent_prompts.append((invocation.tool_name, classification))
            return True

        loop = AssistantLoop(
            provider=provider,
            registry=registry,
            system_prompt=(
                "You are running a temporary, authorized Nova agent validation. "
                "Use custom_insert_probe with value 'llm_probe' exactly once. "
                "Then use custom_select_probe with value 'llm_probe' exactly once. "
                "Only after verifying the selected row, answer briefly."
            ),
            max_iterations=6,
            time_budget_seconds=180,
        )
        observed = []
        async for frame in loop.run(
            thread=thread,
            user_content="Insert llm_probe and verify it with the selected tools.",
            context=context,
            resolve_consent=consent,
        ):
            observed.append(_event(frame))

        calls = [data.get("tool_name") for event, data in observed if event == "tool_call"]
        finished = [data.get("finish_reason") for event, data in observed if event == "done"]
        errors = [data.get("code") for event, data in observed if event == "error"]
        assert calls == ["custom_insert_probe", "custom_select_probe"], (
            calls, errors, pipeline_errors[-1:]
        )
        assert consent_prompts == [("custom_insert_probe", "destructive")]
        assert finished and finished[-1] == "stop", (finished, errors)

        async with root.cursor() as cursor:
            await cursor.execute(f"SELECT value FROM `{database}`.`records` WHERE id = 1")
            assert await cursor.fetchone() == ("llm_probe",)
    finally:
        async with root.cursor() as cursor:
            if created_user:
                with suppress(Exception):
                    await cursor.execute(f"DROP USER IF EXISTS '{username}'")
            if created_role:
                with suppress(Exception):
                    await cursor.execute(f"DROP ROLE IF EXISTS `{role}`")
            if created_database:
                with suppress(Exception):
                    await cursor.execute(f"DROP DATABASE IF EXISTS `{database}` FORCE")
        root.close()
