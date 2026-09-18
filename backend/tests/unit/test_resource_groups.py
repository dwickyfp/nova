"""Unit tests for the Resource Groups module (roadmap #16, NOVA-94).

No engine. The shared query pipeline is replaced with a recorder and the
authenticated user comes from a dependency override. The tests pin:

* CRUD issues the documented ``CREATE/ALTER/DROP RESOURCE GROUP`` statements;
* classifier add/drop serializes the documented classifier attributes;
* quota enforcement is *not* simulated — usage reads the engine's own
  ``SHOW USAGE RESOURCE GROUPS`` output, and the service stores no counters;
* an unsupported attribute or an escaping value is refused before any SQL is
  built;
* every mutating op goes through the audited pipeline.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import deps as deps_module
from app.modules.resource_groups import router as rg_router
from app.modules.resource_groups import service as service_module
from app.modules.resource_groups.schemas import ClassifierSpec, ResourceGroupCreate
from app.modules.resource_groups.service import (
    ResourceGroupError,
    resource_group_service,
)


class _Result:
    def __init__(self, *, columns=None, rows=None, error=None) -> None:
        self.columns = columns or []
        self.rows = rows or []
        self.error = error


class RecordingPipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.responses: dict[str, _Result] = {}
        self.default = _Result()

    async def execute(self, *, sql: str, role: str | None = None, **_: Any) -> _Result:
        self.calls.append((sql, role))
        return self.responses.get(sql, self.default)

    def statements(self) -> list[str]:
        return [sql for sql, _ in self.calls]


@pytest.fixture
def pipeline(monkeypatch):
    fake = RecordingPipeline()
    monkeypatch.setattr(service_module, "query_service", fake)
    return fake


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(rg_router.router, prefix="/api/v1/resource-groups")
    app.dependency_overrides[deps_module.get_current_user] = lambda: {
        "username": "alice",
        "session_id": "s1",
        "roles": ["ACCOUNTADMIN"],
        "active_role": "ACCOUNTADMIN",
        "encrypted_password": "enc",
    }
    return TestClient(app, raise_server_exceptions=False)


async def _create(**kwargs):
    return await resource_group_service.create_resource_group(
        ResourceGroupCreate(**kwargs),
        username="alice",
        encrypted_password="enc",
        session_id="s1",
        role="ACCOUNTADMIN",
    )


# ── CRUD ────────────────────────────────────────────────────────


class TestCrud:
    async def test_create_builds_with_list(self, pipeline):
        await _create(name="etl_group", properties={"cpu_core_limit": 4, "mem_limit": 0.5})
        sql = pipeline.statements()[0]
        assert sql == (
            'CREATE RESOURCE GROUP `etl_group` WITH '
            '("cpu_core_limit" = "4", "mem_limit" = "0.5")'
        )

    async def test_create_forwards_active_role(self, pipeline):
        await _create(name="g", properties={"cpu_core_limit": 1})
        assert pipeline.calls[0][1] == "ACCOUNTADMIN"

    async def test_alter_sets_attributes(self, pipeline):
        await resource_group_service.alter_resource_group(
            "etl_group",
            properties={"concurrency_limit": 10},
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == (
            'ALTER RESOURCE GROUP `etl_group` SET ("concurrency_limit" = "10")'
        )

    async def test_drop(self, pipeline):
        await resource_group_service.drop_resource_group(
            "etl_group",
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == "DROP RESOURCE GROUP `etl_group`"

    async def test_list_maps_engine_rows(self, pipeline):
        pipeline.default = _Result(
            columns=["name", "cpu_core_limit", "mem_limit"],
            rows=[["etl_group", "4", "0.5"]],
        )
        groups = await resource_group_service.list_resource_groups(
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert groups == [
            {
                "name": "etl_group",
                "properties": {"cpu_core_limit": "4", "mem_limit": "0.5"},
                "classifiers": [],
            }
        ]


# ── Classifiers ─────────────────────────────────────────────────


class TestClassifiers:
    async def test_add_serializes_documented_attributes(self, pipeline):
        await resource_group_service.add_classifier(
            "etl_group",
            ClassifierSpec(user="alice", query_type="select"),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == (
            "ALTER RESOURCE GROUP `etl_group` ADD ('user' = 'alice', 'query_type' = 'select')"
        )

    async def test_drop_serializes_classifier(self, pipeline):
        await resource_group_service.drop_classifier(
            "etl_group",
            ClassifierSpec(role="etl_role"),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == (
            "ALTER RESOURCE GROUP `etl_group` DROP ('role' = 'etl_role')"
        )

    async def test_create_with_classifiers_adds_each(self, pipeline):
        await _create(
            name="g",
            properties={"cpu_core_limit": 1},
            classifiers=[{"user": "alice"}, {"role": "etl"}],
        )
        assert pipeline.statements() == [
            'CREATE RESOURCE GROUP `g` WITH ("cpu_core_limit" = "1")',
            "ALTER RESOURCE GROUP `g` ADD ('user' = 'alice')",
            "ALTER RESOURCE GROUP `g` ADD ('role' = 'etl')",
        ]


# ── Quota is the engine's ───────────────────────────────────────


class TestQuotaNotSimulated:
    async def test_usage_reads_engine_surface(self, pipeline):
        pipeline.default = _Result(
            columns=["name", "running", "queued"],
            rows=[["etl_group", "3", "0"], ["adhoc_group", "1", "2"]],
        )
        usage = await resource_group_service.usage(
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == "SHOW USAGE RESOURCE GROUPS"
        assert [(u["name"], u["running"], u["queued"]) for u in usage] == [
            ("etl_group", 3, 0),
            ("adhoc_group", 1, 2),
        ]

    async def test_service_has_no_counter_state(self):
        import inspect

        source = inspect.getsource(service_module)
        # No Nova-side running/queued bookkeeping and no direct connection.
        assert "self._running" not in source
        assert "self._queued" not in source
        assert "asyncmy" not in source


# ── Validation ──────────────────────────────────────────────────


class TestValidation:
    async def test_unsupported_attribute_is_refused(self, pipeline):
        with pytest.raises(ResourceGroupError):
            await _create(name="g", properties={"not_a_real_attr": 1})
        assert pipeline.calls == []

    async def test_empty_properties_is_refused(self, pipeline):
        with pytest.raises(ResourceGroupError):
            await _create(name="g", properties={})
        assert pipeline.calls == []

    @pytest.mark.parametrize("bad", ['1") DROP RESOURCE GROUP `x`', "a;b", "a b"])
    async def test_escaping_attribute_value_is_refused(self, pipeline, bad):
        with pytest.raises(ResourceGroupError):
            await _create(name="g", properties={"cpu_core_limit": bad})
        assert pipeline.calls == []

    @pytest.mark.parametrize("bad", ["a`b", "a b", ""])
    async def test_bad_name_is_refused(self, pipeline, bad):
        with pytest.raises(ResourceGroupError):
            await resource_group_service.drop_resource_group(
                bad,
                username="alice",
                encrypted_password="enc",
                session_id="s1",
            )
        assert pipeline.calls == []

    async def test_engine_error_becomes_400(self, client, pipeline):
        pipeline.default = _Result(error="resource group already exists")
        resp = client.post(
            "/api/v1/resource-groups",
            json={"name": "g", "properties": {"cpu_core_limit": 1}},
        )
        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"]

    def test_usage_endpoint(self, client, pipeline):
        pipeline.default = _Result(
            columns=["name", "running", "queued"], rows=[["g", "3", "0"]]
        )
        resp = client.get("/api/v1/resource-groups/usage")
        assert resp.status_code == 200, resp.text
        assert resp.json()["usage"][0]["running"] == 3


# ── Audit ───────────────────────────────────────────────────────


class TestAudit:
    async def test_every_mutation_goes_through_audited_pipeline(self, pipeline):
        await _create(name="g", properties={"cpu_core_limit": 1})
        await resource_group_service.add_classifier(
            "g",
            ClassifierSpec(user="alice"),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        await resource_group_service.drop_resource_group(
            "g", username="alice", encrypted_password="enc", session_id="s1"
        )
        assert len(pipeline.calls) == 3
