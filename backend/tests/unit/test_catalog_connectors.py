"""Unit tests for catalog connectors and the catalog service's credential rules.

The invariants under test are the credential ones:

- a request can never carry a secret property, so one can never be persisted;
- ``SHOW CREATE CATALOG`` output is redacted before it leaves the service,
  including the two keys StarRocks 4.1.4 returns in full
  (``aws.s3.session_token`` and ``hive.metastore.password``).

Catalogs are created only from a connection; ``test_catalog_sources`` covers
the statements each source builds.

Values are placeholders. No real credential appears in this file.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.modules.catalog import service as catalog_service_module
from app.modules.catalog.connectors import CatalogError, connector_for
from app.modules.catalog.schemas import ExternalCatalogAlter, is_secret_property
from app.modules.catalog.service import CatalogService
from tests.unit.test_catalog_store import MemoryRepo


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
            # The dotted spellings of a multi-word suffix. A final-segment match
            # saw only ``key`` / ``token`` and let these through; the whole tail
            # has to be matched so each spelling of one credential is the same
            # name to both the validator and the redactor.
            "aws.s3.secret.key",
            "azure.account.key",
            "gcp.gcs.private.key",
            "gcp.gcs.service.account.key",
            "aws.s3.session.token",
            "hive.metastore.account.key",
            "azure.account.sas.token",
            "gcp.gcs.service.account.private.key",
        ],
    )
    def test_dotted_multi_word_suffixes_are_recognised(self, key):
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

    @pytest.mark.parametrize(
        "key",
        [
            # A word that merely *ends* in a suffix word as a substring is not a
            # credential: ``monkey`` is not ``key``, ``compassword`` is not
            # ``password``. Anchoring the whole key is what keeps the validator
            # from over-blocking ordinary non-secret properties.
            "iceberg.catalog.warehouse",
            "aws.s3.bucket",
            "hive.metastore.monkey",
            "iceberg.catalog.compassword",
        ],
    )
    def test_partial_word_suffix_is_not_a_secret(self, key):
        assert not is_secret_property(key)

    def test_validator_and_redactor_agree_on_every_spelling(self):
        """The request guard and the statement redactor share one rule.

        The bug this pins was the two sides disagreeing: the redactor masked a
        dotted credential while the validator accepted the key, so a secret
        reached the metadata store and the API response. For every key either
        side treats as a credential, both must.
        """
        from app.common.sql_guard import redact_sql_credentials

        dotted_and_underscore = [
            ("azure.account.key", "azure.account_key"),
            ("gcp.gcs.private.key", "gcp.gcs.private_key"),
            ("gcp.gcs.service.account.key", "gcp.gcs.service_account_key"),
            ("aws.s3.session.token", "aws.s3.session_token"),
        ]
        for dotted, underscore in dotted_and_underscore:
            assert is_secret_property(dotted), dotted
            assert is_secret_property(underscore), underscore
            for key in (dotted, underscore):
                stmt = (
                    "CREATE EXTERNAL CATALOG c PROPERTIES "
                    f"('{key}' = 'LEAK_SENTINEL')"
                )
                assert "LEAK_SENTINEL" not in redact_sql_credentials(stmt), key


class TestAlterRequestRejectsSecrets:
    @pytest.mark.parametrize(
        "key",
        [
            "aws.s3.secret_key",
            "hive.metastore.password",
            "aws.s3.secret.key",
            "azure.account.key",
            "gcp.gcs.service.account.key",
            "aws.s3.session.token",
        ],
    )
    def test_alter_rejects_a_secret_property(self, key):
        with pytest.raises(ValueError):
            ExternalCatalogAlter(properties={key: "PLACEHOLDER"})

class TestAlterAndDropSql:
    def test_alter_builds_set_clause(self):
        sql = connector_for("iceberg").build_alter_sql(
            "lake", ExternalCatalogAlter(properties={"aws.s3.endpoint": "http://minio:29000"})
        )
        assert sql == "ALTER CATALOG lake SET ('aws.s3.endpoint' = 'http://minio:29000')"

    def test_alter_with_nothing_to_change_is_refused(self):
        with pytest.raises(CatalogError):
            connector_for("iceberg").build_alter_sql("lake", ExternalCatalogAlter())

    def test_drop_builds_drop_statement(self):
        assert connector_for("hive").build_drop_sql("lake") == "DROP CATALOG lake"


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
        from app.modules.catalog.connectors import safe_catalog_name as _safe_catalog_name

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
        from app.modules.catalog.connectors import safe_catalog_name as _safe_catalog_name

        with pytest.raises(CatalogError):
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


class FakeRepo(MemoryRepo):
    def __init__(self, catalogs=None):
        super().__init__(catalogs)
        self.retired = []

    async def retire_catalog_objects(self, catalog, *, generation):
        await super().retire_catalog_objects(catalog, generation=generation)
        self.retired.append(catalog)


def catalog_service(repo, monkeypatch):
    @asynccontextmanager
    async def transaction(actor, *, read_only=False):
        assert actor.principal == USER["username"]
        assert actor.active_role == USER["active_role"]
        yield repo
    monkeypatch.setattr(catalog_service_module, "catalog_transaction", transaction)
    return CatalogService()


USER = {
    "username": "analyst",
    "encrypted_password": "enc",
    "session_id": "s1",
    "active_role": "ACCOUNTADMIN",
    "assigned_roles": ["ACCOUNTADMIN"],
}


@pytest.fixture
def quiet_service(monkeypatch):
    """A catalog service whose visibility and post-change sync are stubbed."""

    async def visible(user):
        return [{"name": "lake", "type": "Iceberg", "comment": None, "databases": []}]

    async def no_sync(*args, **kwargs):
        return {}

    async def no_bump():
        return None

    monkeypatch.setattr(catalog_service_module.visibility, "visible_catalogs", visible)
    monkeypatch.setattr(catalog_service_module.visibility, "bump", no_bump)
    monkeypatch.setattr(catalog_service_module, "sync_catalog", no_sync)


class TestServiceReadPathRedacts:
    async def test_show_create_is_redacted(self, quiet_service, monkeypatch):
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

        monkeypatch.setattr(catalog_service_module.db, "execute_system", fake_execute_system)

        response = await catalog_service(FakeRepo(), monkeypatch).get_catalog("lake", USER)
        assert response is not None and response.create_statement is not None
        assert "SESSION_SENTINEL" not in response.create_statement
        assert "PW_SENTINEL" not in response.create_statement

    async def test_persisted_credential_property_is_dropped_on_read(
        self, quiet_service, monkeypatch
    ):
        """A dotted credential already in a store row must not be echoed."""
        repo = FakeRepo(
            [
                {
                    "name": "lake",
                    "catalog_type": "iceberg",
                    "properties": {
                        "azure.account.key": "CLIENT_SECRET_LEAK_ABC",
                        "aws.s3.session.token": "LEAK_DEF",
                        "iceberg.catalog.warehouse": "s3://bucket/wh",
                    },
                }
            ]
        )
        monkeypatch.setattr(
            catalog_service_module.db, "execute_system", _async_return({"rows": []})
        )

        response = await catalog_service(repo, monkeypatch).get_catalog("lake", USER)
        assert response is not None
        assert "azure.account.key" not in response.properties
        assert "aws.s3.session.token" not in response.properties
        assert response.properties["iceberg.catalog.warehouse"] == "s3://bucket/wh"
        assert "CLIENT_SECRET_LEAK_ABC" not in str(response.model_dump())

    async def test_invisible_catalog_has_no_detail(self, quiet_service, monkeypatch):
        executed: list[str] = []

        async def fake_execute_system(sql, *args, **kwargs):
            executed.append(sql)
            return {"rows": []}

        monkeypatch.setattr(catalog_service_module.db, "execute_system", fake_execute_system)
        assert await catalog_service(FakeRepo(), monkeypatch).get_catalog("hidden", USER) is None
        assert executed == []


class TestServiceDrop:
    async def test_drop_marks_the_row_and_its_objects_dropped(
        self, quiet_service, monkeypatch
    ):
        recording = RecordingQueryService()
        monkeypatch.setattr(catalog_service_module, "query_service", recording)
        repo = FakeRepo([{"name": "lake", "catalog_type": "iceberg", "dropped_at": None}])
        await catalog_service(repo, monkeypatch).drop("lake", USER)
        assert recording.calls == ["DROP CATALOG lake"]
        assert repo.catalogs["lake"]["dropped_at"] is not None
        assert repo.retired == ["lake"]


def test_unknown_catalog_type_is_refused():
    with pytest.raises(CatalogError):
        connector_for("paimon")
