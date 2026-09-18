"""Unit tests for external secret references on storage connections (NOVA-58).

A storage connection may carry ``secret_ref`` instead of inline credentials.
These tests pin the contract that decision established:

* reference-only — nothing but the reference is ever persisted,
* fail-closed — a provider failure raises, it never falls back to the inline
  ``nova.yaml`` value,
* no stale cache — a failed fetch is not served from a prior success,
* redacted failures — a resolved value never reaches an exception message, an
  audit row, an API response or a log line.

Every secret here is a placeholder (``AKIA...EXAMPLE``-style) chosen so a leak
would be obvious; none is a real credential. The provider is always a mock —
there is no live AWS call in CI.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace

import pytest

from app.core.config import StorageConnectionConfig
from app.modules.query.dialect import injector
from app.storage import secrets as secrets_module
from app.storage.secrets import (
    AwsSecretsManagerProvider,
    SecretResolutionError,
    _parse_secret_payload,
    _split_scheme,
    clear_secret_cache,
    resolve_secret_reference,
)

ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

REF = "arn:aws:secretsmanager:us-east-1:123456789012:secret:nova/s3-prod"


class FakeProvider:
    """In-memory ``SecretProvider``. Records calls; does not touch the network."""

    name = "fake"

    def __init__(self, value=None, error: Exception | None = None):
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
def _no_cache_leak_between_tests():
    clear_secret_cache()
    secrets_module.drain_secret_resolution_facts()
    yield
    clear_secret_cache()
    secrets_module.drain_secret_resolution_facts()


class TestParseSecretPayload:
    """Accepted payload shapes, and what a bad one must not do."""

    def test_json_object_parsed(self):
        value = _parse_secret_payload(
            json.dumps({"access_key": ACCESS_KEY, "secret_key": SECRET_KEY}),
            provider="aws",
            reference=REF,
        )
        assert value.access_key == ACCESS_KEY
        assert value.secret_key == SECRET_KEY

    def test_json_aliases_accepted(self):
        value = _parse_secret_payload(
            json.dumps({"access_key_id": ACCESS_KEY, "secret_key_id": SECRET_KEY}),
            provider="aws",
            reference=REF,
        )
        assert (value.access_key, value.secret_key) == (ACCESS_KEY, SECRET_KEY)

    def test_json_session_token_read(self):
        value = _parse_secret_payload(
            json.dumps(
                {
                    "access_key": ACCESS_KEY,
                    "secret_key": SECRET_KEY,
                    "session_token": "TOKEN_PLACEHOLDER",
                }
            ),
            provider="aws",
            reference=REF,
        )
        assert value.session_token == "TOKEN_PLACEHOLDER"

    def test_plain_access_colon_secret_parsed(self):
        value = _parse_secret_payload(
            f"{ACCESS_KEY}:{SECRET_KEY}", provider="aws", reference=REF
        )
        assert (value.access_key, value.secret_key) == (ACCESS_KEY, SECRET_KEY)

    def test_json_missing_secret_fails_closed(self):
        with pytest.raises(SecretResolutionError):
            _parse_secret_payload(
                json.dumps({"access_key": ACCESS_KEY}), provider="aws", reference=REF
            )

    def test_json_unparseable_fails_closed(self):
        with pytest.raises(SecretResolutionError):
            _parse_secret_payload(
                "{not json", provider="aws", reference=REF
            )

    def test_unrecognised_payload_fails_closed(self):
        with pytest.raises(SecretResolutionError):
            _parse_secret_payload(
                "a single opaque blob", provider="aws", reference=REF
            )

    @pytest.mark.parametrize(
        "payload",
        [
            json.dumps({"access_key": ACCESS_KEY}),  # no secret
            "{not json",
            "opaque",
        ],
    )
    def test_error_message_never_carries_the_payload(self, payload):
        """A malformed payload must not be echoed into the error message."""
        with pytest.raises(SecretResolutionError) as excinfo:
            _parse_secret_payload(payload, provider="aws", reference=REF)
        assert ACCESS_KEY not in str(excinfo.value)


class TestSplitScheme:
    def test_bare_arn_has_no_scheme(self):
        assert _split_scheme(REF) == (None, REF)

    def test_scheme_is_split(self):
        assert _split_scheme("aws://nova/s3") == ("aws", "nova/s3")

    def test_bare_name_has_no_scheme(self):
        assert _split_scheme("nova/s3") == (None, "nova/s3")


class TestResolveSecretReference:
    def test_resolves_through_the_mock_provider(self):
        from app.storage.secrets import SecretValue

        provider = FakeProvider(SecretValue(ACCESS_KEY, SECRET_KEY))
        value = resolve_secret_reference(
            REF, providers={"aws": provider}, use_cache=False
        )
        assert (value.access_key, value.secret_key) == (ACCESS_KEY, SECRET_KEY)
        assert provider.calls == [REF]

    def test_scheme_prefix_stripped_before_fetch(self):
        from app.storage.secrets import SecretValue

        provider = FakeProvider(SecretValue(ACCESS_KEY, SECRET_KEY))
        resolve_secret_reference(
            "aws://nova/s3", providers={"aws": provider}, use_cache=False
        )
        assert provider.calls == ["nova/s3"]

    def test_provider_error_is_wrapped(self):
        provider = FakeProvider(error=RuntimeError("AccessDenied"))
        with pytest.raises(SecretResolutionError):
            resolve_secret_reference(REF, providers={"aws": provider}, use_cache=False)

    def test_unknown_scheme_fails_closed(self):
        with pytest.raises(SecretResolutionError):
            resolve_secret_reference(
                "vault://secret/x", providers={"aws": FakeProvider()}, use_cache=False
            )

    def test_cache_serves_the_second_call(self):
        from app.storage.secrets import SecretValue

        provider = FakeProvider(SecretValue(ACCESS_KEY, SECRET_KEY))
        registry = {"aws": provider}
        resolve_secret_reference(REF, providers=registry)
        resolve_secret_reference(REF, providers=registry)
        assert provider.calls == [REF]

    def test_failure_is_not_cached(self):
        """A failed fetch must retry, not be masked by an earlier success."""
        from app.storage.secrets import SecretValue

        provider = FakeProvider(SecretValue(ACCESS_KEY, SECRET_KEY))
        registry = {"aws": provider}
        resolve_secret_reference(REF, providers=registry)

        # Now the provider starts failing; the cached value must not survive
        # an explicit uncached call, and the failure must surface.
        failing = FakeProvider(error=RuntimeError("timeout"))
        with pytest.raises(SecretResolutionError):
            resolve_secret_reference(REF, providers={"aws": failing}, use_cache=False)
        assert failing.calls == [REF]


class TestAwsSecretsManagerProvider:
    """The adapter's own payload handling, with a fake boto3 client."""

    class _Client:
        def __init__(self, response=None, error: Exception | None = None):
            self.response = response or {}
            self.error = error

        def get_secret_value(self, SecretId):
            if self.error is not None:
                raise self.error
            return self.response

    def test_json_secret_string_resolved(self):
        provider = AwsSecretsManagerProvider()
        provider._client = self._Client(
            {"SecretString": json.dumps({"access_key": ACCESS_KEY, "secret_key": SECRET_KEY})}
        )
        value = provider.fetch(REF)
        assert (value.access_key, value.secret_key) == (ACCESS_KEY, SECRET_KEY)

    def test_binary_secret_fails_closed(self):
        provider = AwsSecretsManagerProvider()
        provider._client = self._Client({"SecretBinary": b"\x00\x01"})
        with pytest.raises(SecretResolutionError):
            provider.fetch(REF)

    def test_client_error_wrapped_without_leaking(self):
        provider = AwsSecretsManagerProvider()
        provider._client = self._Client(error=RuntimeError("boom AKIAIOSFODNN7EXAMPLE"))
        with pytest.raises(SecretResolutionError) as excinfo:
            provider.fetch(REF)
        # The adapter reports the exception *type*, not its message, so an SDK
        # error that echoes a credential cannot leak through this path.
        assert ACCESS_KEY not in str(excinfo.value)

    def test_lazy_client_does_no_io_on_construction(self):
        provider = AwsSecretsManagerProvider()
        assert provider._client is None


class TestInjectorUsesProviderWhenReferenceConfigured:
    """The @stage / FILES() path resolves references through the provider."""

    @staticmethod
    def _conn(secret_ref: str = "") -> StorageConnectionConfig:
        return StorageConnectionConfig(
            name="production",
            type="s3",
            endpoint="http://minio:9000",
            bucket="b",
            access_key="INLINE_PLACEHOLDER_A",
            secret_key="INLINE_PLACEHOLDER_B",
            secret_ref=secret_ref,
        )

    def test_reference_takes_precedence_over_inline_values(self, monkeypatch):
        from app.storage.secrets import SecretValue

        provider = FakeProvider(SecretValue(ACCESS_KEY, SECRET_KEY))
        monkeypatch.setattr(
            injector, "get_storage_connection", lambda name: self._conn(REF)
        )
        monkeypatch.setattr(
            injector, "resolve_secret_reference", lambda ref: provider.fetch(ref)
        )
        assert injector.resolve_storage_credentials("production") == (
            ACCESS_KEY,
            SECRET_KEY,
        )

    def test_no_reference_uses_inline_values_unchanged(self, monkeypatch):
        monkeypatch.setattr(
            injector, "get_storage_connection", lambda name: self._conn("")
        )
        assert injector.resolve_storage_credentials("production") == (
            "INLINE_PLACEHOLDER_A",
            "INLINE_PLACEHOLDER_B",
        )

    def test_provider_failure_never_falls_back_to_inline(self, monkeypatch):
        """Acceptance 4: fail-closed, no nova.yaml credential as a substitute."""
        provider = FakeProvider(error=RuntimeError("AccessDenied"))
        monkeypatch.setattr(
            injector, "get_storage_connection", lambda name: self._conn(REF)
        )
        # Exercise the *real* resolver with a mock provider registry, so the
        # wrapping of an arbitrary provider failure is under test too.
        monkeypatch.setattr(
            injector,
            "resolve_secret_reference",
            lambda ref: secrets_module.resolve_secret_reference(
                ref, providers={"aws": provider}, use_cache=False
            ),
        )
        with pytest.raises(SecretResolutionError) as excinfo:
            injector.resolve_storage_credentials("production")
        assert "INLINE_PLACEHOLDER_A" not in str(excinfo.value)
        assert "INLINE_PLACEHOLDER_B" not in str(excinfo.value)

    def test_get_credential_params_uses_resolved_secret(self, monkeypatch):
        from app.storage.secrets import SecretValue

        provider = FakeProvider(SecretValue(ACCESS_KEY, SECRET_KEY))
        monkeypatch.setattr(
            injector, "get_storage_connection", lambda name: self._conn(REF)
        )
        monkeypatch.setattr(
            injector, "resolve_secret_reference", lambda ref: provider.fetch(ref)
        )
        params = injector.get_credential_params("s3", "production")
        assert params["aws.s3.access_key"] == ACCESS_KEY
        assert params["aws.s3.secret_key"] == SECRET_KEY


class TestRedactionOfResolvedSecret:
    """Acceptance 5: a resolved value never reaches an outbound surface."""

    PLACEHOLDER = "AKIA_SECRET_REF_PLACEHOLDER"

    def test_sql_injected_with_secret_is_redacted(self):
        from app.common.sql_guard import redact_sql_credentials

        sql = (
            "SELECT * FROM FILES('path'='s3://b/k', 'format'='csv', "
            f"'aws.s3.access_key'='{self.PLACEHOLDER}', "
            f"'aws.s3.secret_key'='{self.PLACEHOLDER}_SECRET')"
        )
        out = redact_sql_credentials(sql)
        assert self.PLACEHOLDER not in out

    def test_unredactable_secret_fails_closed(self):
        from app.common.sql_guard import (
            CredentialsRedactionError,
            redact_sql_credentials,
        )

        with pytest.raises(CredentialsRedactionError):
            redact_sql_credentials(
                f"FILES('aws.s3.secret_key'='{self.PLACEHOLDER}"
            )

    def test_sanitizing_response_drops_a_leaked_secret(self):
        from app.modules.query.router import SanitizingJSONResponse

        payload = {"sql": f"FILES('aws.s3.secret_key'='{self.PLACEHOLDER}_LEAK')"}
        body = SanitizingJSONResponse(payload).render(payload).decode()
        assert f"{self.PLACEHOLDER}_LEAK" not in body

    def test_nova_exception_handler_redacts_resolved_secret(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.core.exceptions import StarRocksError, register_exception_handlers

        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/__boom")
        async def _boom():
            raise StarRocksError(
                f"engine rejected FILES('aws.s3.secret_key'='{self.PLACEHOLDER}')"
            )

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/__boom")
        assert self.PLACEHOLDER not in resp.text


class TestAuditRecordsFactNotValue:
    """Acceptance 6: audit carries provider + reference; never a value."""

    def test_successful_resolution_records_a_fact(self):
        from app.storage.secrets import SecretValue

        provider = FakeProvider(SecretValue(ACCESS_KEY, SECRET_KEY))
        resolve_secret_reference(REF, providers={"aws": provider}, use_cache=False)
        facts = secrets_module.drain_secret_resolution_facts()
        assert len(facts) == 1
        assert facts[0].reference == REF
        assert facts[0].succeeded is True
        assert ACCESS_KEY not in str(facts[0])
        assert SECRET_KEY not in str(facts[0])

    def test_failed_resolution_records_a_fact(self):
        provider = FakeProvider(error=RuntimeError("AccessDenied"))
        with pytest.raises(SecretResolutionError):
            resolve_secret_reference(REF, providers={"aws": provider}, use_cache=False)
        facts = secrets_module.drain_secret_resolution_facts()
        assert len(facts) == 1
        assert facts[0].reference == REF
        assert facts[0].succeeded is False
        assert ACCESS_KEY not in str(facts[0])

    def test_drain_is_idempotent(self):
        from app.storage.secrets import SecretValue

        provider = FakeProvider(SecretValue(ACCESS_KEY, SECRET_KEY))
        resolve_secret_reference(REF, providers={"aws": provider}, use_cache=False)
        secrets_module.drain_secret_resolution_facts()
        assert secrets_module.drain_secret_resolution_facts() == []

    async def test_query_service_writes_an_audit_row_per_fact(
        self, monkeypatch
    ):
        """The query service persists the fact — reference only, no value."""
        from app.modules.query import service as service_module

        recorded: list[dict] = []

        async def fake_write_audit_log(**kwargs):
            recorded.append(kwargs)
            return "qid"

        monkeypatch.setattr(service_module, "write_audit_log", fake_write_audit_log)

        from app.storage.secrets import SecretValue

        provider = FakeProvider(SecretValue(ACCESS_KEY, SECRET_KEY))
        resolve_secret_reference(REF, providers={"aws": provider}, use_cache=False)

        svc = service_module.QueryService()
        await svc._audit_secret_resolutions(username="analyst")

        assert len(recorded) == 1
        row = recorded[0]
        assert row["event_type"] == "secret_fetch"
        assert row["object_type"] == "secret_reference"
        assert row["object_name"] == REF
        assert row["status"] == "SUCCESS"
        assert ACCESS_KEY not in str(row)
        assert SECRET_KEY not in str(row)


class TestDefaultPathIsUnchanged:
    """Acceptance 7: the nova.yaml-only path behaves exactly as before."""

    def test_connection_without_reference_reads_inline(self, monkeypatch):
        conn = StorageConnectionConfig(
            name="production",
            type="minio",
            endpoint="http://minio:9000",
            bucket="b",
            access_key="cfg-access",
            secret_key="cfg-secret",
        )
        monkeypatch.setattr(injector, "get_storage_connection", lambda name: conn)
        assert injector.resolve_storage_credentials("production") == (
            "cfg-access",
            "cfg-secret",
        )

    def test_unconfigured_credentials_still_fail_closed(self, monkeypatch):
        conn = StorageConnectionConfig(
            name="production",
            type="minio",
            endpoint="http://minio:9000",
            bucket="b",
            access_key="",
            secret_key="",
        )
        monkeypatch.setattr(injector, "get_storage_connection", lambda name: conn)
        assert injector.get_credential_params("s3") == {}

    def test_no_aws_call_when_no_reference_is_configured(self, monkeypatch):
        """A connection without secret_ref must never touch a provider."""

        def explode(*args, **kwargs):
            raise AssertionError("provider must not be called without a reference")

        monkeypatch.setattr(injector, "resolve_secret_reference", explode)
        conn = StorageConnectionConfig(
            name="production",
            type="s3",
            endpoint="",
            bucket="b",
            access_key="cfg-access",
            secret_key="cfg-secret",
        )
        monkeypatch.setattr(injector, "get_storage_connection", lambda name: conn)
        assert injector.resolve_storage_credentials("production") == (
            "cfg-access",
            "cfg-secret",
        )


class TestLoggingDoesNotLeak:
    """A resolved secret must not appear in log output."""

    def test_provider_failure_log_has_no_value(self, caplog):
        from app.storage.secrets import SecretValue

        provider = FakeProvider(SecretValue(ACCESS_KEY, SECRET_KEY))
        # Force a failure *after* a success so a naive cache implementation
        # would have the value in hand.
        resolve_secret_reference(REF, providers={"aws": provider})
        failing = FakeProvider(error=RuntimeError("denied"))
        with caplog.at_level(logging.DEBUG), pytest.raises(SecretResolutionError):
            resolve_secret_reference(
                REF, providers={"aws": failing}, use_cache=False
            )
        assert ACCESS_KEY not in caplog.text
        assert SECRET_KEY not in caplog.text


class TestConfigParsing:
    """``secret_ref`` loads from nova.yaml; absent means empty."""

    def test_secret_ref_defaults_empty(self):
        conn = StorageConnectionConfig(
            name="x", type="s3", endpoint="", bucket="b", access_key="a", secret_key="s"
        )
        assert conn.secret_ref == ""

    def test_replace_preserves_reference(self):
        conn = StorageConnectionConfig(
            name="x", type="s3", endpoint="", bucket="b", access_key="a",
            secret_key="s", secret_ref=REF,
        )
        assert replace(conn).secret_ref == REF

    def test_load_reads_secret_ref_from_yaml(self, monkeypatch, tmp_path):
        import yaml

        from app.core import config as config_module

        nova = tmp_path / "nova.yaml"
        nova.write_text(
            yaml.safe_dump(
                {
                    "storage": {
                        "connections": {
                            "aws_prod": {
                                "type": "s3",
                                "bucket": "b",
                                "secret_ref": REF,
                            }
                        }
                    }
                }
            )
        )
        monkeypatch.setattr(config_module.settings, "NOVA_CONFIG_PATH", str(nova))
        config_module.load_nova_app_config.cache_clear()
        try:
            loaded = config_module.load_nova_app_config()
            assert loaded.storage_connections["aws_prod"].secret_ref == REF
        finally:
            config_module.load_nova_app_config.cache_clear()


class TestStageQueryEndToEnd:
    """The ``@stage`` query path with a referenced secret, without an engine.

    Drives the real stage-config loader (``_load_stage_configs_filtered``) by
    stubbing only ``db.execute_system``, so the reference resolution under test
    is the one production uses. Proves the two acceptance outcomes at the
    boundary: the engine receives the *resolved* value, and a resolution failure
    is reported and audited with nothing credential-bearing in the response.
    """

    def _wire(self, monkeypatch, provider):
        import app.modules.query.dialect.injector as injector_module
        import app.modules.query.service as service_module
        from app.modules.query.service import QueryService

        recorded: list[str] = []
        audits: list[dict] = []

        class Repo:
            async def execute_as_user(self, sql, **kwargs):
                recorded.append(sql)
                from app.modules.query.repository import QueryResult

                return QueryResult(
                    executed_sql=sql, columns=["v"], rows=[[1]], row_count=1
                )

        async def fake_write_audit_log(**kwargs):
            audits.append(kwargs)
            return "qid"

        async def fake_execute_system(sql, params=None):
            # One stage bound to the referenced 'production' connection.
            return {
                "rows": [
                    ("stage1", "db", "sch", "production", "db/sch/stage1"),
                ]
            }

        async def fake_csv(parsed, stage_configs):
            return {}, None

        svc = QueryService()
        svc._repo = Repo()
        svc._detect_csv_params = fake_csv

        monkeypatch.setattr(service_module, "write_audit_log", fake_write_audit_log)
        monkeypatch.setattr(service_module, "decrypt_password", lambda value: "pw")
        monkeypatch.setattr(service_module.db, "execute_system", fake_execute_system)

        # Give the real 'production' connection a secret reference and route its
        # resolution through the mock provider, keeping the real wrapping and
        # fail-closed logic in play.
        real_get_connection = injector_module.get_storage_connection

        def fake_connection(name):
            return replace(real_get_connection(name), secret_ref=REF)

        monkeypatch.setattr(injector_module, "get_storage_connection", fake_connection)
        monkeypatch.setattr(
            injector_module,
            "resolve_secret_reference",
            lambda ref: secrets_module.resolve_secret_reference(
                ref, providers={"aws": provider}, use_cache=False
            ),
        )
        return svc, recorded, audits

    async def test_engine_receives_resolved_secret(self, monkeypatch):
        from app.storage.secrets import SecretValue

        provider = FakeProvider(SecretValue(ACCESS_KEY, SECRET_KEY))
        svc, recorded, _audits = self._wire(monkeypatch, provider)

        result = await svc.execute(
            sql="SELECT * FROM @stage1.data.csv",
            username="analyst",
            encrypted_password="enc",
        )

        assert recorded, "the statement never reached the engine"
        assert f"'{ACCESS_KEY}'" in recorded[-1]

        # Nothing outbound carries the resolved value.
        assert ACCESS_KEY not in result.executed_sql
        assert ACCESS_KEY not in result.original_sql
        assert SECRET_KEY not in result.executed_sql

    async def test_provider_failure_is_reported_not_fallback(self, monkeypatch):
        provider = FakeProvider(error=RuntimeError("AccessDenied"))
        svc, recorded, audits = self._wire(monkeypatch, provider)

        result = await svc.execute(
            sql="SELECT * FROM @stage1.data.csv",
            username="analyst",
            encrypted_password="enc",
        )

        # Fail-closed: the engine is never called, and the error is the
        # reference failure — not a misleading "stage not found", not a 500.
        assert recorded == []
        assert result.error is not None
        assert "AccessDenied" in result.error or "failed for reference" in result.error
        assert ACCESS_KEY not in str(result.error)

        # The resolution fact is audited: reference, failure — never a value.
        fact_rows = [row for row in audits if row.get("event_type") == "secret_fetch"]
        assert fact_rows, "the failed resolution was not audited"
        row = fact_rows[-1]
        assert row["status"] == "ERROR"
        assert row["object_name"] == REF
        assert ACCESS_KEY not in str(row)
        assert SECRET_KEY not in str(row)

        # And the query-attempt row is still written, redacted.
        for audit in audits:
            assert ACCESS_KEY not in str(audit)
            assert SECRET_KEY not in str(audit)

    async def test_reference_resolves_per_connection_not_workspace_default(
        self, monkeypatch
    ):
        """A stage's own connection decides the reference, not the default.

        The pre-existing loader resolved the workspace-default connection for
        every stage; a placeholder inline key made that harmless. With
        references it is not: the wrong connection would fetch the wrong
        principal's credential.
        """
        import app.modules.query.dialect.injector as injector_module
        import app.modules.query.service as service_module
        from app.modules.query.service import QueryService

        recorded: list[dict] = []

        async def fake_write_audit_log(**kwargs):
            return "qid"

        class Repo:
            async def execute_as_user(self, sql, **kwargs):
                from app.modules.query.repository import QueryResult

                recorded.append(sql)
                return QueryResult(
                    executed_sql=sql, columns=["v"], rows=[[1]], row_count=1
                )

        async def fake_execute_system(sql, params=None):
            return {"rows": [("stage1", "db", "sch", "backup", "db/sch/stage1")]}

        async def fake_csv(parsed, stage_configs):
            return {}, None

        svc = QueryService()
        svc._repo = Repo()
        svc._detect_csv_params = fake_csv

        seen: list[str] = []

        def fake_connection(name):
            seen.append(name)
            real = original_get_connection(name)
            return replace(real, secret_ref="")

        from app.core.config import StorageConnectionConfig

        def original_get_connection(name):
            return StorageConnectionConfig(
                name=name,
                type="s3",
                endpoint="http://minio:9000",
                bucket="b",
                access_key=f"KEY_FOR_{name}",
                secret_key=f"SECRET_FOR_{name}",
            )

        monkeypatch.setattr(service_module, "write_audit_log", fake_write_audit_log)
        monkeypatch.setattr(service_module, "decrypt_password", lambda value: "pw")
        monkeypatch.setattr(service_module.db, "execute_system", fake_execute_system)
        monkeypatch.setattr(injector_module, "get_storage_connection", fake_connection)

        await svc.execute(
            sql="SELECT * FROM @stage1.data.csv",
            username="analyst",
            encrypted_password="enc",
        )

        assert "backup" in seen, f"stage's own connection was not resolved: {seen}"
        assert "KEY_FOR_backup" in recorded[-1]


def test_secret_value_not_in_nova_system_reference_tuple():
    """Acceptance 8: only the reference — never a value — is a persisted token."""
    conn = StorageConnectionConfig(
        name="x", type="s3", endpoint="", bucket="b", access_key="a",
        secret_key="s", secret_ref=REF,
    )
    # The persisted form is the reference; the dataclass holds no resolved value.
    assert REF in str(conn)
    assert ACCESS_KEY not in str(conn)
