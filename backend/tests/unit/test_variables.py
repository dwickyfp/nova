"""Unit tests for the Variables & Settings module (roadmap #8, NOVA-94).

No engine. The shared query pipeline is replaced with a recorder and the
authenticated user comes from a dependency override. The tests pin:

* ``SHOW VARIABLES`` / ``SHOW GLOBAL VARIABLES`` browse, search and paginate;
* session-vs-global toggle selects the right engine statement;
* ``SET`` renders the value safely (bare token vs quoted literal) and is
  refused when a name or value could inject a second statement;
* ``SET GLOBAL`` / ``SET SESSION`` / ``= DEFAULT`` shapes;
* the password-policy surface writes only the supplied fields as ``SET GLOBAL``;
* every mutation goes through the audited pipeline.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import deps as deps_module
from app.modules.variables import router as variables_router
from app.modules.variables import service as service_module
from app.modules.variables.schemas import (
    PasswordPolicy,
    VariableScope,
    VariableSetRequest,
)
from app.modules.variables.service import VariableError, variable_service

SHOW_COLUMNS = ["Variable_name", "Value"]
SHOW_ROWS = [
    ["query_timeout", "300"],
    ["exec_mem_limit", "2147483648"],
    ["enable_profile", "false"],
    ["time_zone", "Asia/Shanghai"],
]


class _Result:
    def __init__(self, *, columns=None, rows=None, error=None) -> None:
        self.columns = columns or []
        self.rows = rows or []
        self.error = error


class RecordingPipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.default = _Result(columns=SHOW_COLUMNS, rows=[list(r) for r in SHOW_ROWS])

    async def execute(self, *, sql: str, role: str | None = None, **_: Any) -> _Result:
        self.calls.append((sql, role))
        return self.default

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
    app.include_router(variables_router.router, prefix="/api/v1/variables")
    app.dependency_overrides[deps_module.get_current_user] = lambda: {
        "username": "alice",
        "session_id": "s1",
        "roles": ["ACCOUNTADMIN"],
        "active_role": "ACCOUNTADMIN",
        "encrypted_password": "enc",
    }
    return TestClient(app, raise_server_exceptions=False)


# ── Browse / search / paginate ──────────────────────────────────


class TestBrowse:
    async def test_session_uses_show_variables(self, pipeline):
        await variable_service.list_variables(
            scope=VariableScope.SESSION,
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == "SHOW VARIABLES"

    async def test_global_uses_show_global_variables(self, pipeline):
        await variable_service.list_variables(
            scope=VariableScope.GLOBAL,
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == "SHOW GLOBAL VARIABLES"

    async def test_search_filters_by_name(self, pipeline):
        page = await variable_service.list_variables(
            scope=VariableScope.SESSION,
            search="timeout",
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert [v["name"] for v in page["variables"]] == ["query_timeout"]
        assert page["total"] == 1

    async def test_pagination_slices_after_filter(self, pipeline):
        page = await variable_service.list_variables(
            scope=VariableScope.SESSION,
            limit=2,
            offset=1,
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert [v["name"] for v in page["variables"]] == [
            "exec_mem_limit",
            "enable_profile",
        ]
        assert page["count"] == 2
        assert page["total"] == 4
        assert page["limit"] == 2
        assert page["offset"] == 1

    def test_http_browse_carries_scope(self, client, pipeline):
        resp = client.get("/api/v1/variables", params={"scope": "global"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["scope"] == "global"
        assert pipeline.statements()[0] == "SHOW GLOBAL VARIABLES"


# ── SET ─────────────────────────────────────────────────────────


class TestSet:
    async def test_set_session_numeric(self, pipeline):
        await variable_service.set_variable(
            VariableSetRequest(scope=VariableScope.SESSION, name="query_timeout", value="600"),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == "SET SESSION query_timeout = 600"

    async def test_set_global_bool(self, pipeline):
        await variable_service.set_variable(
            VariableSetRequest(
                scope=VariableScope.GLOBAL, name="enable_query_queue", value="true"
            ),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == "SET GLOBAL enable_query_queue = true"

    async def test_set_string_is_quoted(self, pipeline):
        await variable_service.set_variable(
            VariableSetRequest(
                scope=VariableScope.SESSION, name="time_zone", value="Asia/Shanghai"
            ),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == "SET SESSION time_zone = 'Asia/Shanghai'"

    async def test_reset_uses_default(self, pipeline):
        await variable_service.set_variable(
            VariableSetRequest(
                scope=VariableScope.SESSION, name="query_timeout", reset=True
            ),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == "SET SESSION query_timeout = DEFAULT"

    async def test_set_forwards_active_role(self, pipeline):
        await variable_service.set_variable(
            VariableSetRequest(scope=VariableScope.GLOBAL, name="enable_profile", value="true"),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
            role="ACCOUNTADMIN",
        )
        assert pipeline.calls[0][1] == "ACCOUNTADMIN"

    def test_http_set(self, client, pipeline):
        resp = client.post(
            "/api/v1/variables/set",
            json={"scope": "session", "name": "query_timeout", "value": "600"},
        )
        assert resp.status_code == 200, resp.text
        assert pipeline.statements()[0] == "SET SESSION query_timeout = 600"


# ── Validation ──────────────────────────────────────────────────


class TestValidation:
    async def test_value_with_semicolon_is_refused_by_schema(self):
        with pytest.raises(ValidationError):
            VariableSetRequest(
                scope=VariableScope.SESSION,
                name="query_timeout",
                value="600; DROP TABLE t",
            )

    @pytest.mark.parametrize("bad", ["a b", "a;b", "a`b", "1=2"])
    async def test_bad_name_is_refused(self, pipeline, bad):
        with pytest.raises(ValidationError):
            VariableSetRequest(scope=VariableScope.SESSION, name=bad)

    async def test_missing_value_without_reset_is_refused(self, pipeline):
        with pytest.raises(VariableError):
            await variable_service.set_variable(
                VariableSetRequest(scope=VariableScope.SESSION, name="query_timeout"),
                username="alice",
                encrypted_password="enc",
                session_id="s1",
            )
        assert pipeline.calls == []

    async def test_engine_error_becomes_400(self, client, pipeline):
        pipeline.default = _Result(error="unknown variable")
        resp = client.post(
            "/api/v1/variables/set",
            json={"scope": "session", "name": "no_such_var", "value": "1"},
        )
        assert resp.status_code == 400
        assert "unknown variable" in resp.json()["detail"]


# ── Password policy ─────────────────────────────────────────────


class TestPasswordPolicy:
    async def test_get_reads_global_and_maps_documented_keys(self, pipeline):
        policy = await variable_service.get_password_policy(
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert "password_lifetime" in policy
        assert "validate_password_length" in policy
        assert pipeline.statements()[0] == "SHOW GLOBAL VARIABLES"

    async def test_set_writes_only_supplied_fields(self, pipeline):
        await variable_service.set_password_policy(
            PasswordPolicy(password_lifetime=90, validate_password=True),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements() == [
            "SET GLOBAL password_lifetime = 90",
            "SET GLOBAL validate_password = true",
        ]

    async def test_empty_policy_writes_nothing(self, pipeline):
        result = await variable_service.set_password_policy(
            PasswordPolicy(),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert result["updated"] == 0
        assert pipeline.calls == []

    def test_http_password_policy_roundtrip(self, client, pipeline):
        resp = client.post(
            "/api/v1/variables/password-policy",
            json={"password_lifetime": 60, "password_history": 5},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["updated"] == 2
        assert pipeline.statements() == [
            "SET GLOBAL password_lifetime = 60",
            "SET GLOBAL password_history = 5",
        ]


# ── Audit ───────────────────────────────────────────────────────


class TestAudit:
    async def test_mutations_go_through_audited_pipeline(self, pipeline):
        await variable_service.set_variable(
            VariableSetRequest(scope=VariableScope.GLOBAL, name="enable_profile", value="true"),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        await variable_service.set_password_policy(
            PasswordPolicy(password_lifetime=30),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert len(pipeline.calls) == 2

    async def test_service_opens_no_direct_connection(self):
        import inspect

        source = inspect.getsource(service_module)
        assert "asyncmy" not in source
        assert "db.execute_system" not in source
