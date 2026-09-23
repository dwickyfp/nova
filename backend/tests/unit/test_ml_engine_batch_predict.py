"""Regression tests for the ``batch_predict`` / ``prediction_sql`` path.

``MLEngineService.batch_predict`` executed the caller's ``prediction_sql`` raw
on a connection carrying real storage credentials — same root cause as the
``training_sql`` defect, and now routed through the same
``sql_pipeline`` preparation (NOVA-28).

These are L1: the service is driven with a fake connection and a recording
cursor, so no engine is involved. The credential invariant is asserted on
structure rather than on the secret value (STANDARD SS10 rule 4).
"""

import pytest

from app.common.sql_guard import _POPULATED_CREDENTIAL, _normalized_value
from app.core.exceptions import ForbiddenSQLError
from app.modules.ml_engine.service import MLEngineService

STAGE_ACCESS_KEY = "STAGE_CFG_ACCESS"
STAGE_SECRET_KEY = "STAGE_CFG_SECRET"


def _stage_configs(stage_name: str = "stage1"):
    from app.modules.query.dialect.translator import StorageConfig

    return {
        stage_name: StorageConfig(
            storage_type="s3",
            endpoint="http://minio:9000",
            bucket="bucket",
            base_prefix="db/schema/stage1",
            access_key=STAGE_ACCESS_KEY,
            secret_key=STAGE_SECRET_KEY,
            region="us-east-1",
        )
    }


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(
        "app.modules.query.sql_pipeline.get_credential_params",
        lambda *a, **kw: {"aws.s3.access_key": "PIPE_A", "aws.s3.secret_key": "PIPE_S"},
    )

    async def stage_configs(parsed, **kwargs):
        configs = _stage_configs()
        if any(ref.stage_name not in configs for ref in parsed.stage_refs):
            raise ValueError("Stage not found")
        return parsed, {ref.start: configs[ref.stage_name] for ref in parsed.stage_refs}

    monkeypatch.setattr(
        "app.modules.query.service.query_service._resolve_stage_refs", stage_configs
    )
    return MLEngineService()


def _no_populated_credential(sql: str) -> bool:
    for match in _POPULATED_CREDENTIAL.finditer(sql):
        value = _normalized_value(match.group("value"))
        if value and value != "***":
            return False
    return True


class TestPredictionSqlIsGuarded:
    """The guard must run on prediction_sql exactly as on the worksheet."""

    async def test_blocked_statement_never_reaches_the_engine(self, service, monkeypatch):
        executed: list[str] = []

        class Cursor:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, sql, params=None):
                executed.append(sql)

            async def fetchone(self):
                return None

            async def fetchall(self):
                return []

        class Conn:
            def cursor(self, *args, **kwargs):
                return Cursor()

            def close(self):
                pass

        async def fake_connect():
            return Conn()

        monkeypatch.setattr(service, "_connect", fake_connect)

        with pytest.raises(ForbiddenSQLError):
            await service.batch_predict(
                model_alias="m", prediction_sql="DROP ROLE ACCOUNTADMIN", database_name=None
            )

        # The guard fired during preparation, before any statement ran.
        assert executed == []

    @pytest.mark.parametrize(
        "sql",
        [
            "REVOKE ALL ON *.* FROM ROLE ACCOUNTADMIN",
            "ALTER ROLE ACCOUNTADMIN",
            "DROP /* ; */ ROLE ACCOUNTADMIN",
            "SELECT 1; DROP ROLE ACCOUNTADMIN",
        ],
    )
    async def test_every_accountadmin_form_is_blocked(self, service, sql):
        with pytest.raises(ForbiddenSQLError):
            await service.batch_predict(
                model_alias="m", prediction_sql=sql, database_name=None
            )


class TestPredictionSqlTranslation:
    """`@stage` in prediction SQL is rewritten and credential-injected."""

    async def test_stage_reference_is_translated(self, service):
        engine_sql = await service._prepare_user_sql(
            sql="SELECT * FROM @stage1.data.csv",
            database_name=None,
            what="prediction",
        )
        assert "@stage1" not in engine_sql
        assert "FILES(" in engine_sql
        assert f"'{STAGE_ACCESS_KEY}'" in engine_sql

    async def test_plain_select_passes_through_unchanged(self, service):
        engine_sql = await service._prepare_user_sql(
            sql="SELECT age FROM customers",
            database_name=None,
            what="prediction",
        )
        assert engine_sql == "SELECT age FROM customers"
        assert "aws.s3.access_key" not in engine_sql

    async def test_translation_error_names_the_stage(self, service):
        """An unresolvable stage fails as a ValueError, not a raw dialect error."""
        with pytest.raises(ValueError, match="not found|unknown|stage"):
            await service._prepare_user_sql(
                sql="SELECT * FROM @nosuchstage.data.csv",
                database_name=None,
                what="prediction",
            )


class TestPredictionSqlCredentialInvariant:
    """No credential may survive into the redacted form of prediction SQL."""

    async def test_redacted_form_is_credential_free(self, service):
        engine_sql = await service._prepare_user_sql(
            sql="SELECT * FROM @stage1.data.csv",
            database_name=None,
            what="prediction",
        )
        redacted = service._redacted_user_sql(engine_sql)

        # The engine form carries what @stage needs...
        assert STAGE_ACCESS_KEY in engine_sql
        assert STAGE_SECRET_KEY in engine_sql
        # ...and the form that may leave the process carries none of it.
        assert STAGE_ACCESS_KEY not in redacted
        assert STAGE_SECRET_KEY not in redacted
        assert _no_populated_credential(redacted)
