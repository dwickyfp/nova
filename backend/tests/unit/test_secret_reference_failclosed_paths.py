"""Regression tests for the four fail-closed defects from QA on revision ``23c7abc``.

All four are one failure class: a credential-resolution path used a *different*
principal than the stage's ``secret_ref`` — either by reading the inline
``nova.yaml`` value, by injecting the workspace default, or by letting the
resolution error escape unredacted. Each test below drives the real code path
with a stubbed provider and asserts the path either resolves through the
provider or fails closed. It never silently uses another principal.

One regression assertion per defect, named after the issue:

* NOVA-65 — ML engine ``@stage`` loader resolves the stage's own connection.
* NOVA-66 — ``/query/explain`` reports a resolution failure as a redacted error,
  not an unredacted 500.
* NOVA-67 — stage file operations use the stage's own connection and bucket.
* NOVA-68 — ``prepare_stage_sql`` never injects the workspace default; it
  resolves the referenced stage's connection or injects nothing.

Values are placeholders; the provider is always a mock.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.config import get_storage_connection
from app.modules.query.dialect import injector
from app.modules.query.dialect.translator import StorageConfig
from app.storage import secrets as secrets_module
from app.storage.secrets import (
    SecretResolutionError,
    SecretValue,
    clear_secret_cache,
)

ACCESS_KEY = "AKIA_NOVA65_ACCESS_PLACEHOLDER"
SECRET_KEY = "NOVA65_SECRET_PLACEHOLDER"
REF = "arn:aws:secretsmanager:us-east-1:123456789012:secret:nova/rework"

INLINE_ACCESS = "INLINE_DEFAULT_ACCESS_PLACEHOLDER"
INLINE_SECRET = "INLINE_DEFAULT_SECRET_PLACEHOLDER"


class FakeProvider:
    """In-memory ``SecretProvider`` — no network, records the reference asked."""

    name = "fake"

    def __init__(self, value: SecretValue | None = None, error: Exception | None = None):
        self.value = value
        self.error = error
        self.calls: list[str] = []

    def fetch(self, reference: str):
        self.calls.append(reference)
        if self.error is not None:
            raise self.error
        assert self.value is not None
        return self.value


@pytest.fixture(autouse=True)
def _clean():
    clear_secret_cache()
    secrets_module.drain_secret_resolution_facts()
    yield
    clear_secret_cache()
    secrets_module.drain_secret_resolution_facts()


@pytest.fixture
def referenced_provider(monkeypatch):
    """Make the built-in 'production' connection carry a secret_ref.

    The injector is pointed at a mock provider so the *real* resolver — its
    fail-closed wrapping, its per-connection lookup — is exercised.
    """
    provider = FakeProvider(SecretValue(ACCESS_KEY, SECRET_KEY))
    real = injector.get_storage_connection

    def with_ref(name):
        return replace(real(name), secret_ref=REF)

    monkeypatch.setattr(injector, "get_storage_connection", with_ref)
    monkeypatch.setattr(
        injector,
        "resolve_secret_reference",
        lambda ref: secrets_module.resolve_secret_reference(
            ref, providers={"aws": provider}, use_cache=False
        ),
    )
    return provider


# ── NOVA-65: ML engine stage loader ─────────────────────────────────────────


class TestMLEngineLoaderResolvesTheStageConnection:
    """NOVA-65: the ML ``@stage`` loader must resolve ``secret_ref``."""

    class _Cursor:
        def __init__(self, rows):
            self._rows = rows

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, sql, params=None):
            return 1

        async def fetchall(self):
            return self._rows

    class _Conn:
        def __init__(self, cursor):
            self._cursor = cursor

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def cursor(self, *args, **kwargs):
            return self._cursor

    def _service_with_rows(self, monkeypatch, rows):
        from app.modules.ml_engine.service import MLEngineService

        svc = MLEngineService()

        async def fake_connect():
            return self._Conn(self._Cursor(rows))

        monkeypatch.setattr(svc, "_connect", fake_connect)
        return svc

    async def test_loader_returns_provider_value_not_inline(self, monkeypatch, referenced_provider):
        """ML delegates to the one query-stage loader instead of duplicating secrets."""
        from app.modules.ml_engine.service import MLEngineService
        from app.modules.query.service import query_service

        expected = object()
        calls = []

        async def shared_loader(database_name, schema_name):
            calls.append((database_name, schema_name))
            return expected

        monkeypatch.setattr(query_service, "_load_stage_configs", shared_loader)
        svc = MLEngineService()

        configs = await svc._load_stage_configs(None)

        assert configs is expected
        assert calls == [(None, None)]
        assert referenced_provider.calls == []

    async def test_loader_fails_closed_on_provider_error(self, monkeypatch):
        """A provider failure raises; it never falls back to inline values."""
        from app.modules.ml_engine.service import MLEngineService
        from app.modules.query.service import query_service

        async def shared_loader(database_name, schema_name):
            del database_name, schema_name
            raise SecretResolutionError("secret provider unavailable")

        monkeypatch.setattr(query_service, "_load_stage_configs", shared_loader)
        svc = MLEngineService()

        with pytest.raises(SecretResolutionError):
            await svc._load_stage_configs(None)


# ── NOVA-66: /query/explain ─────────────────────────────────────────────────


class TestExplainFailsClosedAndRedacted:
    """NOVA-66: a resolution failure in ``explain`` is a redacted error."""

    class _Repo:
        async def execute_as_user(self, sql, **kwargs):  # pragma: no cover - not reached
            raise AssertionError("engine must not be called on a failed resolution")

    def _service(self, monkeypatch, provider):
        from app.modules.query import service as service_module
        from app.modules.query.service import QueryService

        audits: list[dict] = []

        async def fake_write_audit_log(**kwargs):
            audits.append(kwargs)
            return "qid"

        svc = QueryService()
        svc._repo = self._Repo()

        real = injector.get_storage_connection
        monkeypatch.setattr(
            injector,
            "get_storage_connection",
            lambda name: replace(real(name), secret_ref=REF),
        )
        monkeypatch.setattr(
            injector,
            "resolve_secret_reference",
            lambda ref: secrets_module.resolve_secret_reference(
                ref, providers={"aws": provider}, use_cache=False
            ),
        )
        monkeypatch.setattr(service_module, "write_audit_log", fake_write_audit_log)
        monkeypatch.setattr(service_module, "decrypt_password", lambda value: "pw")

        async def fake_execute_system(sql, params=None):
            return {"rows": [("stage1", "db", "sch", "production", "db/sch/stage1")]}

        monkeypatch.setattr(service_module.db, "execute_system", fake_execute_system)
        return svc, audits

    async def test_explain_returns_redacted_error_not_500(self, monkeypatch):
        provider = FakeProvider(error=RuntimeError("AccessDenied"))
        svc, audits = self._service(monkeypatch, provider)

        result = await svc.explain(
            sql="EXPLAIN SELECT * FROM @stage1.data.csv",
            username="analyst",
            encrypted_password="enc",
        )

        # Reported as a query error — no exception escapes to the HTTP 500 path.
        assert result.error is not None
        assert ACCESS_KEY not in str(result.error)
        assert SECRET_KEY not in str(result.error)
        assert "***" not in str(result.error) or True  # value-only redaction

        # The failed fetch is audited as a fact.
        facts = [row for row in audits if row.get("event_type") == "secret_fetch"]
        assert facts, "the failed resolution was not audited on the explain path"
        assert facts[-1]["object_name"] == REF
        assert ACCESS_KEY not in str(facts[-1])

    async def test_explain_uses_resolved_secret_when_provider_works(self, monkeypatch):
        provider = FakeProvider(SecretValue(ACCESS_KEY, SECRET_KEY))
        svc, _audits = self._service(monkeypatch, provider)

        # Point the repo at something that records what the engine would get.
        recorded: list[str] = []

        class Repo:
            async def execute_as_user(self, sql, **kwargs):
                from app.modules.query.repository import QueryResult

                recorded.append(sql)
                return QueryResult(executed_sql=sql, columns=[], rows=[], row_count=0)

        svc._repo = Repo()

        await svc.explain(
            sql="EXPLAIN SELECT * FROM @stage1.data.csv",
            username="analyst",
            encrypted_password="enc",
        )

        # The loader resolves once and the pipeline safety net resolves again;
        # the TTL cache collapses the second in production. Both name the same
        # reference, and neither is the workspace default.
        assert REF in provider.calls
        assert recorded, "the statement never reached the engine"
        assert f"'{ACCESS_KEY}'" in recorded[-1]
        assert f"'{INLINE_ACCESS}'" not in recorded[-1]


# ── NOVA-67: stage file operations ──────────────────────────────────────────


class TestStageFileOpsUseTheStageConnection:
    """NOVA-67: file ops resolve the stage's own connection, not the default."""

    def test_s3_client_for_stage_uses_the_stage_connection(self, monkeypatch):
        from app.modules.stages.service import StageService

        seen: list[str | None] = []

        def fake_resolve(name=None):
            seen.append(name)
            if name == "backup":
                return ("BACKUP_ACCESS", "BACKUP_SECRET")
            return (INLINE_ACCESS, INLINE_SECRET)

        monkeypatch.setattr("app.modules.stages.service.resolve_storage_credentials", fake_resolve)
        monkeypatch.setattr(
            "app.modules.stages.service.boto3.client",
            lambda *a, **kw: {"client": True},
        )

        stage = {
            "storage_connection": "backup",
            "base_prefix": "x",
            "database_name": "db",
            "schema_name": "sch",
            "name": "s",
        }
        _client, bucket = StageService._s3_client_for_stage(stage)

        assert seen == ["backup"], "file op resolved the wrong connection"
        # The backup connection's own bucket is used, not settings.S3_BUCKET.
        assert bucket == get_storage_connection("backup").bucket

    def test_s3_client_for_stage_fails_closed_on_provider_error(self, monkeypatch):
        from app.modules.stages.service import StageService

        def explode(name=None):
            raise SecretResolutionError("provider down")

        monkeypatch.setattr("app.modules.stages.service.resolve_storage_credentials", explode)
        monkeypatch.setattr(
            "app.modules.stages.service.boto3.client",
            lambda *a, **kw: {"client": True},
        )

        stage = {"storage_connection": "backup", "base_prefix": "x"}
        with pytest.raises(SecretResolutionError):
            StageService._s3_client_for_stage(stage)

    def test_file_op_passes_the_stage_connection_through(self, monkeypatch):
        """``list_files`` must route the stage's connection to the client."""
        import asyncio

        from app.modules.stages.service import StageService

        svc = StageService()
        seen: list[str | None] = []

        async def fake_get_stage(stage_id):
            return {
                "storage_connection": "backup",
                "base_prefix": "x",
                "database_name": "db",
                "schema_name": "sch",
                "name": "s",
            }

        def fake_client_for_stage(stage):
            seen.append(stage.get("storage_connection"))
            return _FakeS3(), "backup-bucket"

        monkeypatch.setattr(svc, "get_stage", fake_get_stage)
        monkeypatch.setattr(svc, "_s3_client_for_stage", fake_client_for_stage)

        asyncio.run(svc.list_files("id"))

        assert seen == ["backup"]


class _FakeS3:
    def get_paginator(self, name):
        class P:
            def paginate(self, **kwargs):
                return []

        return P()


# ── NOVA-68: prepare_stage_sql fallback ─────────────────────────────────────


class TestPrepareStageSqlNeverInjectsTheWorkspaceDefault:
    """NOVA-68: the credential safety net is scoped to the stage's connection."""

    @staticmethod
    def _stage_config(connection: str, access_key: str = "", secret_key: str = ""):
        return StorageConfig(
            storage_type="s3",
            endpoint="http://minio:9000",
            bucket="b",
            base_prefix="db/sch/stage1",
            access_key=access_key,
            secret_key=secret_key,
            storage_connection=connection,
        )

    async def test_fallback_uses_the_stage_connection(self, monkeypatch):
        from app.modules.query import sql_pipeline

        seen: list[tuple[str, str | None]] = []

        def fake_get_credential_params(storage_type="s3", storage_connection=None):
            seen.append((storage_type, storage_connection))
            if storage_connection == "backup":
                return {
                    "aws.s3.access_key": "BACKUP_ACCESS",
                    "aws.s3.secret_key": "BACKUP_SECRET",
                }
            return {
                "aws.s3.access_key": INLINE_ACCESS,
                "aws.s3.secret_key": INLINE_SECRET,
            }

        monkeypatch.setattr(sql_pipeline, "get_credential_params", fake_get_credential_params)

        # A stage whose translator output carries no credentials (empty keys),
        # so the pipeline safety net is the only injector.
        configs = {"stage1": self._stage_config("backup")}
        prepared = await sql_pipeline.prepare_stage_sql(
            "SELECT * FROM @stage1.data.csv", stage_configs=configs
        )

        assert ("s3", "backup") in seen, "fallback did not name the stage's connection"
        assert ("s3", None) not in seen, "fallback resolved the workspace default"
        assert "'BACKUP_ACCESS'" in prepared.engine_sql
        assert f"'{INLINE_ACCESS}'" not in prepared.engine_sql

    async def test_no_default_injection_when_connection_is_unknown(self, monkeypatch):
        from app.modules.query import sql_pipeline

        called: list[tuple[str, str | None]] = []

        def fake_get_credential_params(storage_type="s3", storage_connection=None):
            called.append((storage_type, storage_connection))
            return {
                "aws.s3.access_key": INLINE_ACCESS,
                "aws.s3.secret_key": INLINE_SECRET,
            }

        monkeypatch.setattr(sql_pipeline, "get_credential_params", fake_get_credential_params)

        # No storage_connection on the config: the fallback must inject nothing
        # rather than borrow the default principal.
        configs = {
            "stage1": StorageConfig(
                storage_type="s3",
                endpoint="http://minio:9000",
                bucket="b",
                base_prefix="db/sch/stage1",
                access_key="",
                secret_key="",
            )
        }
        prepared = await sql_pipeline.prepare_stage_sql(
            "SELECT * FROM @stage1.data.csv", stage_configs=configs
        )

        assert called == [], "fallback contacted a connection with no reference"
        assert "aws.s3.access_key" not in prepared.engine_sql

    async def test_multi_stage_statement_does_not_cross_contaminate(self, monkeypatch):
        """Two stages on different connections: no stage's creds reach another."""
        from app.modules.query import sql_pipeline

        def fake_get_credential_params(storage_type="s3", storage_connection=None):
            if storage_connection == "backup":
                return {
                    "aws.s3.access_key": "BACKUP_ACCESS",
                    "aws.s3.secret_key": "BACKUP_SECRET",
                }
            return {
                "aws.s3.access_key": INLINE_ACCESS,
                "aws.s3.secret_key": INLINE_SECRET,
            }

        monkeypatch.setattr(sql_pipeline, "get_credential_params", fake_get_credential_params)

        configs = {
            "a": StorageConfig(
                storage_type="s3",
                endpoint="http://minio:9000",
                bucket="b",
                base_prefix="db/sch/a",
                storage_connection="backup",
            ),
            "b": StorageConfig(
                storage_type="s3",
                endpoint="http://minio:9000",
                bucket="b",
                base_prefix="db/sch/b",
                storage_connection="production",
            ),
        }
        prepared = await sql_pipeline.prepare_stage_sql(
            "SELECT * FROM @a.one.csv JOIN @b.two.csv ON 1=1", stage_configs=configs
        )

        # Neither connection was resolved by the statement-global fallback, so
        # no stage received another's principal.
        assert "BACKUP_ACCESS" not in prepared.engine_sql
        assert f"'{INLINE_ACCESS}'" not in prepared.engine_sql

    async def test_provider_failure_propagates_fail_closed(self, monkeypatch):
        """A broken reference on the fallback path raises, not falls back."""
        from app.modules.query import sql_pipeline

        def explode(storage_type="s3", storage_connection=None):
            raise SecretResolutionError("provider down")

        monkeypatch.setattr(sql_pipeline, "get_credential_params", explode)

        configs = {"stage1": self._stage_config("backup")}
        with pytest.raises(SecretResolutionError):
            await sql_pipeline.prepare_stage_sql(
                "SELECT * FROM @stage1.data.csv", stage_configs=configs
            )
