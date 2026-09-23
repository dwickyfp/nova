"""Regression tests for the ``training_sql`` execution path in ``ml_engine``.

``MLEngineService`` executed the caller's ``training_sql`` raw on a connection
that carries real storage credentials — no SQL guard, no ``@stage`` translation,
no credential injection, no redaction (NOVA-28; recorded at ``README.md:553``).
These tests pin the fixed behaviour and, most importantly, the invariant that no
credential material reaches anything that leaves the process.

Placed at L1: the service methods are driven with a fake connection and a
recording cursor, so no engine or Docker stack is involved. The end-to-end
real-StarRocks proof of the ``@stage`` rewrite belongs at L3 and is a separate
suite.
"""

import pyarrow as pa
import pytest

from app.common.sql_guard import _POPULATED_CREDENTIAL, _normalized_value
from app.core.exceptions import ForbiddenSQLError
from app.modules.ml_engine.artifacts.store import MemoryArtifactStore
from app.modules.ml_engine.execution.job_runner import InlineJobRunner
from app.modules.ml_engine.service import MLEngineService

ACCESS_KEY = "AKIA_TRAINING_ACCESS"
SECRET_KEY = "TRAINING_SECRET"

#: Credentials the translator reads from the *stage config* and injects into
#: FILES() itself. Distinct from the pair above so a test can tell which
#: injection path supplied the value.
STAGE_ACCESS_KEY = "STAGE_CFG_ACCESS"
STAGE_SECRET_KEY = "STAGE_CFG_SECRET"


def _stage_configs(stage_name: str = "stage1"):
    """A stage-config map whose injected credentials are recognisable."""
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


class RecordingCursor:
    """Minimal asyncmy cursor stand-in that records every statement."""

    def __init__(self, rows: list | None = None, description=None) -> None:
        self.executed: list[str] = []
        self._rows = rows if rows is not None else []
        self.description = description

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.executed.append(sql)
        return 1

    async def fetchall(self):
        return self._rows


def _conn_with(cursor: RecordingCursor):
    class Conn:
        def __init__(self) -> None:
            self.closed = False

        def cursor(self, *args, **kwargs):
            return cursor

        def close(self) -> None:
            self.closed = True

    return Conn()


@pytest.fixture
def service(monkeypatch):
    """MLEngineService with credential injection and stage configs stubbed."""
    monkeypatch.setattr(
        "app.modules.query.sql_pipeline.get_credential_params",
        lambda *a, **kw: {
            "aws.s3.access_key": ACCESS_KEY,
            "aws.s3.secret_key": SECRET_KEY,
        },
    )

    return MLEngineService()


class TestTrainingSqlIsGuarded:
    """The guard must run on training_sql exactly as it does on the worksheet."""

    async def test_blocked_statement_never_reaches_the_engine(self, service, monkeypatch):
        cursor = RecordingCursor()
        monkeypatch.setattr(service, "_connect", lambda: _async_return(_conn_with(cursor)))

        with pytest.raises(ForbiddenSQLError):
            await service._prepare_user_sql(
                sql="DROP ROLE ACCOUNTADMIN", database_name=None, what="training"
            )

        # Nothing was executed — the guard fired before a connection was used.
        assert cursor.executed == []

    @pytest.mark.parametrize(
        "sql",
        [
            "REVOKE ALL ON *.* FROM ROLE ACCOUNTADMIN",
            "ALTER ROLE ACCOUNTADMIN",
            "DROP /* ; */ ROLE ACCOUNTADMIN",
        ],
    )
    async def test_every_accountadmin_form_is_blocked(self, service, sql):
        with pytest.raises(ForbiddenSQLError):
            await service._prepare_user_sql(sql=sql, database_name=None, what="training")

    async def test_multistatement_script_cannot_hide_a_blocked_tail(self, service):
        """A guard anchored on the whole blob would miss everything after `;`."""
        with pytest.raises(ForbiddenSQLError):
            await service._prepare_user_sql(
                sql="SELECT 1; DROP ROLE ACCOUNTADMIN", database_name=None, what="training"
            )


class TestTrainingSqlStageTranslation:
    """`@stage` in training SQL must be rewritten before the engine sees it."""

    async def test_stage_reference_is_rewritten_not_sent_raw(self, service, monkeypatch):
        async def stage_configs(parsed, **kwargs):
            return parsed, {
                ref.start: _stage_configs()[ref.stage_name] for ref in parsed.stage_refs
            }

        monkeypatch.setattr(
            "app.modules.query.service.query_service._resolve_stage_refs", stage_configs
        )

        engine_sql = await service._prepare_user_sql(
            sql="SELECT * FROM @stage1.data.csv", database_name=None, what="training"
        )

        # Translated to FILES() and credential-bearing. The translator injects
        # the credentials from the stage config itself, so the assertion is on
        # those values (`k`/`s`) rather than the pipeline's fallback pair.
        assert "@stage1" not in engine_sql
        assert "FILES(" in engine_sql
        assert f"'{STAGE_ACCESS_KEY}'" in engine_sql
        assert f"'{STAGE_SECRET_KEY}'" in engine_sql

    async def test_plain_select_passes_through_unchanged(self, service):
        engine_sql = await service._prepare_user_sql(
            sql="SELECT age, churned FROM customers", database_name=None, what="training"
        )
        assert engine_sql == "SELECT age, churned FROM customers"
        assert "aws.s3.access_key" not in engine_sql


class TestNoCredentialMaterialEscapes:
    """The Nova hard invariant: credentials never reach logs, storage, or errors.

    Asserted on structure (a populated credential assignment is absent) rather
    than on the secret value, per STANDARD SS10 rule 4.
    """

    async def test_redacted_form_is_credential_free(self, service, monkeypatch):
        async def stage_configs(parsed, **kwargs):
            return parsed, {
                ref.start: _stage_configs()[ref.stage_name] for ref in parsed.stage_refs
            }

        monkeypatch.setattr(
            "app.modules.query.service.query_service._resolve_stage_refs", stage_configs
        )

        engine_sql = await service._prepare_user_sql(
            sql="SELECT * FROM @stage1.data.csv", database_name=None, what="training"
        )
        redacted = service._redacted_user_sql(engine_sql)

        # The engine form carries the credentials @stage needs...
        assert STAGE_ACCESS_KEY in engine_sql
        assert STAGE_SECRET_KEY in engine_sql
        # ...and the persisted/logged form carries none of them.
        assert STAGE_ACCESS_KEY not in redacted
        assert STAGE_SECRET_KEY not in redacted
        assert _no_populated_credential(redacted)

    async def test_persistence_stores_the_redacted_form(self, monkeypatch):
        """NOVA_SYSTEM.ML_MODELS.training_sql must hold no credential value.

        The stored statement is what an operator reads back and what a later
        audit inspects; it is on AGENTS.md's never-store-credentials list.
        """
        captured: dict[str, str] = {}

        class Source:
            async def stream(self, sql, security):
                del security
                assert "FILES(" in sql
                yield pa.record_batch(
                    [pa.array(range(30)), pa.array([float(i % 2) for i in range(30)])],
                    names=["age", "churned"],
                )

        class Repository:
            async def reserve_version(self, spec, *, feature_columns):
                del feature_columns
                captured["training_sql"] = spec.input_sql
                return "model-1", 1

            async def register_version(self, **kwargs):
                return None

            async def record_run(self, **kwargs):
                return None

        from tests.unit.ml_fakes import MemoryEphemeralRepository

        svc = MLEngineService(
            data_source=Source(),
            job_runner=InlineJobRunner(),
            artifact_store=MemoryArtifactStore(),
            repository=Repository(),
            ephemeral_repository=MemoryEphemeralRepository(),
        )
        monkeypatch.setattr(
            "app.modules.query.sql_pipeline.get_credential_params",
            lambda *a, **kw: {
                "aws.s3.access_key": ACCESS_KEY,
                "aws.s3.secret_key": SECRET_KEY,
            },
        )

        async def stage_configs(parsed, **kwargs):
            return parsed, {
                ref.start: _stage_configs()[ref.stage_name] for ref in parsed.stage_refs
            }

        monkeypatch.setattr(
            "app.modules.query.service.query_service._resolve_stage_refs", stage_configs
        )

        await svc.train_model(
            model_name="m",
            model_type="regression",
            algorithm="linear",
            training_sql="SELECT age, churned FROM @stage1.data.csv",
            target_column="churned",
            feature_columns=["age"],
            hyperparameters=None,
            test_size=0.0,
            database_name=None,
            as_system=True,
        )

        stored = captured["training_sql"]
        # The translator's stage credentials are the ones in play here, and none
        # of them may reach the row that is persisted.
        assert STAGE_ACCESS_KEY not in stored
        assert STAGE_SECRET_KEY not in stored
        # Registry metadata keeps Nova's credential-free logical syntax; the
        # translated FILES() statement is execution-only.
        assert "@stage1.data.csv" in stored
        assert _no_populated_credential(stored)


def _async_return(value):
    async def _inner():
        return value

    return _inner()


def _no_populated_credential(sql: str) -> bool:
    """True when no credential parameter is still bound to a real value."""
    for match in _POPULATED_CREDENTIAL.finditer(sql):
        value = _normalized_value(match.group("value"))
        if value and value != "***":
            return False
    return True
