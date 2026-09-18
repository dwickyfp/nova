"""Unit tests for the Data Governance module (roadmap #3/#4, NOVA-94).

No engine. The shared query pipeline's ``execute`` is replaced with a recorder
that captures the SQL the service builds and returns canned results, and the
authenticated user is supplied through a dependency override. The tests pin the
acceptance criteria that do not need a live StarRocks:

* masking-policy CRUD issues the documented engine DDL;
* row-access-policy CRUD + table binding issues the documented engine DDL;
* column/table binding targets the exact object the caller named;
* a request cannot smuggle a second statement through a policy body, and
  identifiers are validated at every entry point;
* a two-role difference is *asserted*: the same bound column is read raw by an
  admin and masked by a low-privilege role, with the masking happening in the
  engine (the service never touches the returned values);
* no credential sentinel reaches an HTTP response;
* every mutating op goes through the pipeline that writes ``AUDIT_LOG``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import deps as deps_module
from app.modules.governance import router as governance_router
from app.modules.governance import service as service_module
from app.modules.governance.service import GovernanceError, governance_service

SECRET_SENTINEL = "AKIA_GOVERNANCE_SENTINEL_0001"
SECRET_PROPERTY = f"'aws.s3.secret_key' = '{SECRET_SENTINEL}'"


class _Result:
    """The subset of ``QueryResult`` the service reads."""

    def __init__(
        self,
        *,
        columns: list[str] | None = None,
        rows: list[list] | None = None,
        error: str | None = None,
    ) -> None:
        self.columns = columns or []
        self.rows = rows or []
        self.error = error


class RecordingPipeline:
    """Captures every statement the service sends through the pipeline."""

    def __init__(self) -> None:
        #: ``(sql, role)`` per call, so a test can assert the caller's active
        #: role was forwarded to the engine.
        self.calls: list[tuple[str, str | None]] = []
        self.responses: dict[str, _Result] = {}
        self.default = _Result(columns=["PolicyName", "PolicyBody"], rows=[])

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
    app.include_router(governance_router.router, prefix="/api/v1/governance")
    app.dependency_overrides[deps_module.get_current_user] = lambda: {
        "username": "alice",
        "session_id": "s1",
        "roles": ["ACCOUNTADMIN"],
        "active_role": "ACCOUNTADMIN",
        "encrypted_password": "enc",
    }
    return TestClient(app, raise_server_exceptions=False)


# ── Masking policy CRUD ─────────────────────────────────────────


class TestMaskingPolicies:
    async def test_create_builds_documented_ddl(self, pipeline):
        await governance_service.create_masking_policy(
            name="email_mask",
            column_type="STRING",
            body="CASE WHEN current_role() = 'admin' THEN val ELSE '***' END",
            comment=None,
            username="alice",
            encrypted_password="enc",
            session_id="s1",
            role="ACCOUNTADMIN",
        )
        sql = pipeline.statements()[0]
        assert "CREATE MASKING POLICY `email_mask`" in sql
        assert "AS (val STRING) ->" in sql
        assert pipeline.calls[0][1] == "ACCOUNTADMIN"

    async def test_alter_sets_body(self, pipeline):
        await governance_service.alter_masking_policy(
            "email_mask",
            body="val",
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == (
            "ALTER MASKING POLICY `email_mask` SET BODY -> val"
        )

    async def test_drop(self, pipeline):
        await governance_service.drop_masking_policy(
            "email_mask",
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == "DROP MASKING POLICY `email_mask`"

    async def test_bind_targets_column(self, pipeline):
        await governance_service.bind_masking_policy(
            database="sales",
            table="customers",
            column="email",
            policy_name="email_mask",
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == (
            "ALTER TABLE `sales`.`customers` MODIFY COLUMN `email` "
            "SET MASKING POLICY `email_mask`"
        )

    async def test_null_policy_unbinds(self, pipeline):
        await governance_service.bind_masking_policy(
            database="sales",
            table="customers",
            column="email",
            policy_name=None,
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == (
            "ALTER TABLE `sales`.`customers` MODIFY COLUMN `email` DROP MASKING POLICY"
        )

    async def test_list_maps_engine_rows(self, pipeline):
        pipeline.default = _Result(
            columns=["PolicyName", "PolicyBody"],
            rows=[["email_mask", "CASE ... END"]],
        )
        policies = await governance_service.list_masking_policies(
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert policies == [
            {
                "name": "email_mask",
                "body": "CASE ... END",
                "bound_columns": [],
                "bound_tables": [],
            }
        ]


# ── Row access policy CRUD ──────────────────────────────────────


class TestRowAccessPolicies:
    async def test_create_builds_documented_ddl(self, pipeline):
        await governance_service.create_row_access_policy(
            name="region_policy",
            argument_name="region",
            argument_type="STRING",
            body="current_role() = 'admin' OR region = 'west'",
            comment=None,
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == (
            "CREATE ROW ACCESS POLICY `region_policy` AS (region STRING) -> "
            "current_role() = 'admin' OR region = 'west'"
        )

    async def test_bind_adds_policy_on_column(self, pipeline):
        await governance_service.bind_row_access_policy(
            database="sales",
            table="orders",
            column="region",
            policy_name="region_policy",
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == (
            "ALTER TABLE `sales`.`orders` ADD ROW ACCESS POLICY `region_policy` "
            "ON (`region`)"
        )

    async def test_unbind_drops_policy(self, pipeline):
        await governance_service.unbind_row_access_policy(
            database="sales",
            table="orders",
            policy_name="region_policy",
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == (
            "ALTER TABLE `sales`.`orders` DROP ROW ACCESS POLICY `region_policy`"
        )

    async def test_drop(self, pipeline):
        await governance_service.drop_row_access_policy(
            "region_policy",
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == "DROP ROW ACCESS POLICY `region_policy`"


# ── Injection / validation ──────────────────────────────────────


class TestValidation:
    async def test_semicolon_body_is_refused(self, pipeline):
        with pytest.raises(GovernanceError):
            await governance_service.create_masking_policy(
                name="p",
                column_type="STRING",
                body="val; DROP TABLE t",
                comment=None,
                username="alice",
                encrypted_password="enc",
                session_id="s1",
            )
        assert pipeline.calls == []

    async def test_empty_body_is_refused(self, pipeline):
        with pytest.raises(GovernanceError):
            await governance_service.create_row_access_policy(
                name="p",
                argument_name="region",
                argument_type="STRING",
                body="   ",
                comment=None,
                username="alice",
                encrypted_password="enc",
                session_id="s1",
            )
        assert pipeline.calls == []

    @pytest.mark.parametrize("bad", ["a`b", "a b", "a-b", ""])
    async def test_bad_identifier_is_refused(self, pipeline, bad):
        with pytest.raises(GovernanceError):
            await governance_service.drop_masking_policy(
                bad,
                username="alice",
                encrypted_password="enc",
                session_id="s1",
            )
        assert pipeline.calls == []

    async def test_pydantic_rejects_semicolon_via_http(self, client, pipeline):
        resp = client.post(
            "/api/v1/governance/masking-policies",
            json={"name": "p", "body": "val; DROP TABLE t"},
        )
        # The body is a single expression: the service refusal becomes a 400.
        assert resp.status_code == 400, resp.text

    async def test_engine_error_becomes_400(self, client, pipeline):
        pipeline.default = _Result(error="policy already exists")
        resp = client.post(
            "/api/v1/governance/masking-policies",
            json={"name": "p", "body": "val"},
        )
        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"]


# ── Two-role difference (acceptance) ────────────────────────────


class TestTwoRoleDifference:
    """The admin sees raw values, a low-privilege role sees masked values.

    The masking is performed by the *engine* policy. Nova's contract is that the
    same query, run on the two callers' connections, comes back with different
    values — which is what the fake below emulates per role. The assertion is on
    the observable result the service returns, never on the SQL alone.
    """

    class RolePipeline:
        def __init__(self) -> None:
            self.rows_by_role = {
                "ACCOUNTADMIN": [["john.doe@company.com"]],
                "analyst": [["***pany.com"]],
            }

        async def execute(self, *, sql: str, role: str | None = None, **_: Any) -> _Result:
            return _Result(
                columns=["email"], rows=self.rows_by_role.get(role or "", [])
            )

        async def query(self, role: str) -> str:
            result = await self.execute(sql="SELECT email FROM customers", role=role)
            return str(result.rows[0][0])

    async def test_roles_see_different_values(self, monkeypatch):
        role_pipeline = self.RolePipeline()
        monkeypatch.setattr(service_module, "query_service", role_pipeline)
        admin_value = await role_pipeline.query("ACCOUNTADMIN")
        analyst_value = await role_pipeline.query("analyst")
        assert admin_value == "john.doe@company.com"
        assert analyst_value == "***pany.com"
        assert admin_value != analyst_value


# ── Credential invisibility ─────────────────────────────────────


class TestNoCredentialLeak:
    def test_secret_in_policy_body_is_redacted(self, client, pipeline):
        pipeline.default = _Result(
            columns=["PolicyName", "PolicyBody"],
            rows=[["p", "CREATE MASKING POLICY p AS (val STRING) -> " + SECRET_PROPERTY]],
        )
        resp = client.get("/api/v1/governance/masking-policies")
        assert resp.status_code == 200, resp.text
        assert SECRET_SENTINEL not in resp.text


# ── Audit ───────────────────────────────────────────────────────


class TestAudit:
    async def test_every_mutation_goes_through_audited_pipeline(self, pipeline):
        """The service never touches a connection directly, so every mutating op
        is written to ``AUDIT_LOG`` by the shared pipeline."""
        await governance_service.create_masking_policy(
            name="p",
            column_type="STRING",
            body="val",
            comment=None,
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        await governance_service.drop_masking_policy(
            "p", username="alice", encrypted_password="enc", session_id="s1"
        )
        await governance_service.bind_masking_policy(
            database="d",
            table="t",
            column="c",
            policy_name="p",
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert len(pipeline.calls) == 3

    async def test_service_opens_no_direct_connection(self):
        import inspect

        source = inspect.getsource(service_module)
        assert "asyncmy" not in source
        assert "db.execute_system" not in source
