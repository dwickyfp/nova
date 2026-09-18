"""Unit tests for the external catalog surface (NOVA-62).

The invariants under test are the credential ones:

- a request can never carry a secret property, so one can never be persisted;
- the DDL builders resolve credentials from the shared storage resolver and are
  the *only* place a secret enters a statement;
- ``SHOW CREATE CATALOG`` output is redacted before it leaves the service,
  including the two keys StarRocks 4.1.4 returns in full
  (``aws.s3.session_token`` and ``hive.metastore.password``).

Values are placeholders. No real credential appears in this file.
"""

from __future__ import annotations

import pytest

from app.modules.external_catalogs import service as service_module
from app.modules.external_catalogs.schemas import (
    CatalogType,
    ExternalCatalogAlter,
    ExternalCatalogCreate,
    MetastoreType,
    is_secret_property,
)
from app.modules.external_catalogs.service import (
    ExternalCatalogError,
    ExternalCatalogService,
    build_storage_credential_params,
)

ACCESS_KEY = "AKIA_TESTVALUE_123"
SECRET_KEY = "SECRET_TESTVALUE_456"


class FakeStorageConnection:
    def __init__(self, endpoint="http://minio:29000", region="us-east-1"):
        self.endpoint = endpoint
        self.region = region
        self.bucket = "nova-stages"


@pytest.fixture
def catalog_env(monkeypatch):
    """Resolve storage credentials from a fixed connection, never real config."""
    monkeypatch.setattr(
        service_module,
        "resolve_storage_credentials",
        lambda storage_connection=None: (ACCESS_KEY, SECRET_KEY),
    )
    monkeypatch.setattr(
        service_module,
        "get_storage_connection",
        lambda name: FakeStorageConnection(),
    )
    return service_module


class TestSecretPropertyDetection:
    @pytest.mark.parametrize(
        "key",
        [
            "aws.s3.access_key",
            "aws.s3.secret_key",
            "aws.s3.session_token",
            "azure.blob.shared_key",
            "gcp.gcs.service_account_private_key",
            "hive.metastore.password",
            "iceberg.catalog.oauth2.credential",
            "iceberg.catalog.jdbc.password",
        ],
    )
    def test_secret_keys_are_recognised(self, key):
        assert is_secret_property(key)

    @pytest.mark.parametrize(
        "key",
        [
            "hive.metastore.uris",
            "iceberg.catalog.type",
            "iceberg.catalog.uri",
            "aws.s3.endpoint",
            "aws.s3.region",
        ],
    )
    def test_non_secret_keys_are_not_flagged(self, key):
        assert not is_secret_property(key)


class TestCreateRequestRejectsSecrets:
    @pytest.mark.parametrize(
        "key",
        ["aws.s3.secret_key", "hive.metastore.password", "aws.s3.session_token"],
    )
    def test_create_rejects_a_secret_property(self, key):
        with pytest.raises(ValueError):
            ExternalCatalogCreate(
                name="lake",
                type=CatalogType.ICEBERG,
                metastore_uri="thrift://hms:9083",
                properties={key: "PLACEHOLDER"},
            )

    @pytest.mark.parametrize(
        "key",
        ["aws.s3.secret_key", "hive.metastore.password"],
    )
    def test_alter_rejects_a_secret_property(self, key):
        with pytest.raises(ValueError):
            ExternalCatalogAlter(properties={key: "PLACEHOLDER"})

    def test_create_accepts_non_secret_properties(self):
        body = ExternalCatalogCreate(
            name="lake",
            type=CatalogType.ICEBERG,
            metastore_uri="thrift://hms:9083",
            properties={"iceberg.catalog.warehouse": "s3://bucket/wh"},
        )
        assert body.properties["iceberg.catalog.warehouse"] == "s3://bucket/wh"


class TestCredentialParamsReuseStorageResolver:
    def test_params_use_resolved_credentials(self, catalog_env):
        params = build_storage_credential_params("production")
        assert params["aws.s3.access_key"] == ACCESS_KEY
        assert params["aws.s3.secret_key"] == SECRET_KEY

    def test_no_credentials_means_no_params(self, monkeypatch):
        monkeypatch.setattr(
            service_module,
            "resolve_storage_credentials",
            lambda storage_connection=None: ("", ""),
        )
        assert build_storage_credential_params("production") == {}

    def test_endpoint_is_rewritten_for_the_engine(self, catalog_env):
        # A host-side endpoint must become the docker-internal one StarRocks can
        # resolve; otherwise every catalog read fails with connection refused.
        params = build_storage_credential_params("production")
        assert params["aws.s3.endpoint"] == "http://minio:29000"
        assert params["aws.s3.enable_path_style_access"] == "true"
        assert params["aws.s3.enable_ssl"] == "false"


class TestCreateSql:
    def _body(self, **overrides):
        data = {
            "name": "lake",
            "type": CatalogType.ICEBERG,
            "metastore_type": MetastoreType.HMS,
            "metastore_uri": "thrift://hms:9083",
            "storage_connection": "production",
        }
        data.update(overrides)
        return ExternalCatalogCreate(**data)

    def test_iceberg_hms_maps_to_hive_catalog_type(self, catalog_env):
        sql = ExternalCatalogService().build_create_sql(self._body())
        assert sql.startswith("CREATE EXTERNAL CATALOG lake PROPERTIES (")
        assert "'type' = 'iceberg'" in sql
        # 4.1.4 rejects the doc's ``hms`` alias; ``hive`` is the accepted value.
        assert "'iceberg.catalog.type' = 'hive'" in sql
        assert "'hive.metastore.uris' = 'thrift://hms:9083'" in sql

    def test_iceberg_rest_uses_catalog_uri(self, catalog_env):
        sql = ExternalCatalogService().build_create_sql(
            self._body(metastore_type=MetastoreType.REST, metastore_uri="http://rest:8181")
        )
        assert "'iceberg.catalog.type' = 'rest'" in sql
        assert "'iceberg.catalog.uri' = 'http://rest:8181'" in sql

    def test_hive_catalog_uses_metastore_uris(self, catalog_env):
        sql = ExternalCatalogService().build_create_sql(
            self._body(type=CatalogType.HIVE, metastore_type=MetastoreType.HMS)
        )
        assert "'type' = 'hive'" in sql
        assert "'hive.metastore.uris' = 'thrift://hms:9083'" in sql

    def test_create_sql_carries_resolved_credentials(self, catalog_env):
        sql = ExternalCatalogService().build_create_sql(self._body())
        assert f"'{ACCESS_KEY}'" in sql
        assert f"'{SECRET_KEY}'" in sql

    def test_comment_is_quoted(self, catalog_env):
        sql = ExternalCatalogService().build_create_sql(
            self._body(comment="Iceberg lake")
        )
        assert "COMMENT 'Iceberg lake'" in sql

    def test_embedded_quote_is_escaped(self, catalog_env):
        sql = ExternalCatalogService().build_create_sql(
            self._body(properties={"iceberg.catalog.warehouse": "s3://b/it's"})
        )
        assert "'s3://b/it''s'" in sql


class TestAlterAndDropSql:
    def test_alter_builds_set_clause(self):
        sql = ExternalCatalogService().build_alter_sql(
            "lake", ExternalCatalogAlter(properties={"aws.s3.endpoint": "http://minio:29000"})
        )
        assert sql == "ALTER CATALOG lake SET ('aws.s3.endpoint' = 'http://minio:29000')"

    def test_alter_with_nothing_to_change_is_refused(self):
        with pytest.raises(ExternalCatalogError):
            ExternalCatalogService().build_alter_sql("lake", ExternalCatalogAlter())

    def test_drop_builds_drop_statement(self):
        assert ExternalCatalogService().build_drop_sql("lake") == "DROP CATALOG lake"


class TestCatalogNameValidation:
    """A path segment is not trusted just because the create body was validated."""

    @pytest.mark.parametrize(
        "name",
        [
            "ok_name",
            "_leading",
            "Mixed123",
        ],
    )
    def test_valid_identifiers_pass(self, name):
        from app.modules.external_catalogs.service import _safe_catalog_name

        assert _safe_catalog_name(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "a b",
            "a;DROP CATALOG x",
            "`backtick`",
            "1starts_with_digit",
            "",
            "a.b",
        ],
    )
    def test_invalid_identifiers_are_refused(self, name):
        from app.modules.external_catalogs.service import _safe_catalog_name

        with pytest.raises(ExternalCatalogError):
            _safe_catalog_name(name)


class TestRedactionOfShowCreate:
    """The engine mask is not the guarantee — the redactor is."""

    def test_unmasked_engine_values_are_redacted(self):
        from app.common.sql_guard import redact_sql_credentials

        engine_output = (
            "CREATE EXTERNAL CATALOG `lake`\n"
            "PROPERTIES (\"aws.s3.session_token\" = \"SESSION_TOKEN_SENTINEL\",\n"
            "\"hive.metastore.password\" = \"HMS_PW_SENTINEL\")"
        )
        redacted = redact_sql_credentials(engine_output)
        assert "SESSION_TOKEN_SENTINEL" not in redacted
        assert "HMS_PW_SENTINEL" not in redacted
        assert "'***'" in redacted

    def test_engine_masked_fragments_are_still_redacted(self):
        from app.common.sql_guard import redact_sql_credentials

        engine_output = 'PROPERTIES ("aws.s3.access_key" = "AK******01")'
        redacted = redact_sql_credentials(engine_output)
        assert "AK******01" not in redacted
        assert "'***'" in redacted

    def test_dotted_catalog_credential_key_is_redacted(self):
        from app.common.sql_guard import redact_sql_credentials

        redacted = redact_sql_credentials(
            'PROPERTIES ("gcp.gcs.service_account_private_key" = "PRIVATE_KEY_SENTINEL")'
        )
        assert "PRIVATE_KEY_SENTINEL" not in redacted

    def test_non_secret_catalog_properties_survive(self):
        from app.common.sql_guard import redact_sql_credentials

        sql = (
            'PROPERTIES ("hive.metastore.uris" = "thrift://hms:9083", '
            '"iceberg.catalog.type" = "hive")'
        )
        assert redact_sql_credentials(sql) == sql


class RecordingQueryService:
    """Capture statements sent to the engine and their audit forms."""

    def __init__(self, error: str | None = None):
        self.calls: list[str] = []
        self.audit_sql: list[str] = []
        self._error = error

    async def execute(self, sql, username, encrypted_password, session_id=None, **kwargs):
        from app.common.sql_guard import redact_sql_credentials
        from app.modules.query.repository import QueryResult

        self.calls.append(sql)
        self.audit_sql.append(redact_sql_credentials(sql))
        return QueryResult(executed_sql=sql, error=self._error)


def _async_return(value):
    async def _fake(*args, **kwargs):
        return value

    return _fake



class TestServiceReadPathRedacts:
    async def test_show_create_is_redacted(self, catalog_env, monkeypatch):
        async def fake_execute_system(sql, *args, **kwargs):
            return {
                "rows": [
                    (
                        "lake",
                        "CREATE EXTERNAL CATALOG `lake` PROPERTIES "
                        '("aws.s3.session_token" = "SESSION_SENTINEL", '
                        '"hive.metastore.password" = "PW_SENTINEL")',
                    )
                ]
            }

        monkeypatch.setattr(
            "app.core.database.db.execute_system", fake_execute_system
        )
        monkeypatch.setattr(
            service_module.external_catalog_repo,
            "get_by_name",
            _async_return(None),
        )

        response = await ExternalCatalogService().get("lake")
        assert response.create_statement is not None
        assert "SESSION_SENTINEL" not in response.create_statement
        assert "PW_SENTINEL" not in response.create_statement

    async def test_metadata_row_never_stores_a_secret(self, catalog_env, monkeypatch):
        recorded: dict = {}

        async def fake_upsert(**kwargs):
            recorded.update(kwargs)

        monkeypatch.setattr(
            service_module.external_catalog_repo, "get_by_name", _async_return(None)
        )
        monkeypatch.setattr(service_module.external_catalog_repo, "upsert", fake_upsert)
        monkeypatch.setattr(
            service_module.external_catalog_repo,
            "list_all",
            _async_return([]),
        )

        service = ExternalCatalogService()
        recording = RecordingQueryService()
        monkeypatch.setattr(service_module, "query_service", recording)
        monkeypatch.setattr(service, "get", _async_return(None))

        body = ExternalCatalogCreate(
            name="lake",
            type=CatalogType.ICEBERG,
            metastore_uri="thrift://hms:9083",
            storage_connection="production",
        )
        await service.create(
            body,
            username="analyst",
            encrypted_password="enc",
            session_id="s1",
        )

        flat = str(recorded)
        assert ACCESS_KEY not in flat
        assert SECRET_KEY not in flat
        assert recorded["storage_connection"] == "production"

    async def test_audit_form_has_no_credentials(self, catalog_env, monkeypatch):
        monkeypatch.setattr(
            service_module.external_catalog_repo, "get_by_name", _async_return(None)
        )
        monkeypatch.setattr(
            service_module.external_catalog_repo, "list_all", _async_return([])
        )

        async def fake_upsert(**kwargs):
            return None

        monkeypatch.setattr(service_module.external_catalog_repo, "upsert", fake_upsert)

        service = ExternalCatalogService()
        recording = RecordingQueryService()
        monkeypatch.setattr(service_module, "query_service", recording)
        monkeypatch.setattr(service, "get", _async_return(None))

        body = ExternalCatalogCreate(
            name="lake",
            type=CatalogType.ICEBERG,
            metastore_uri="thrift://hms:9083",
            storage_connection="production",
        )
        await service.create(
            body,
            username="analyst",
            encrypted_password="enc",
            session_id="s1",
        )

        # The engine gets the real credentials...
        engine_sql = recording.calls[-1]
        assert ACCESS_KEY in engine_sql and SECRET_KEY in engine_sql
        # ...the audit form does not.
        audit_sql = recording.audit_sql[-1]
        assert ACCESS_KEY not in audit_sql
        assert SECRET_KEY not in audit_sql

    async def test_engine_error_is_surfaced(self, catalog_env, monkeypatch):
        monkeypatch.setattr(
            service_module.external_catalog_repo, "get_by_name", _async_return(None)
        )
        service = ExternalCatalogService()
        monkeypatch.setattr(
            service_module, "query_service", RecordingQueryService(error="denied")
        )

        body = ExternalCatalogCreate(
            name="lake",
            type=CatalogType.HIVE,
            metastore_uri="thrift://hms:9083",
        )
        with pytest.raises(ExternalCatalogError):
            await service.create(
                body,
                username="analyst",
                encrypted_password="enc",
                session_id="s1",
            )


