"""Application roles admit only the published test epoch on the existing PG17 fixture."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg.conninfo import conninfo_to_dict
from pydantic import SecretStr

from app.core.config import MetadataRuntimeSettings
from app.core.metadata_bootstrap import WORKLOAD_ROLES
from app.core.metadata_db import MetadataSchemaMismatch
from app.core.metadata_migrations import migrate
from app.core.metadata_process import (
    PROCESS_WORKLOADS,
    check_metadata,
    close_process_metadata,
    open_process_metadata,
)
from app.core.metadata_runtime import metadata_db
from app.core.metadata_schema import load_migrations
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401

pytestmark = pytest.mark.metadata_pg


def runtime_settings(test_environment, epoch):
    connection = conninfo_to_dict(test_environment.role_config("nova_meta_web").dsn)
    return MetadataRuntimeSettings(
        host=connection["host"],
        hostaddr=connection.get("hostaddr", ""),
        port=int(connection["port"]),
        sslrootcert=Path(connection["sslrootcert"]),
        passwords=SecretStr(
            json.dumps(
                {
                    role: test_environment.credentials.passwords[role].get_secret_value()
                    for role in WORKLOAD_ROLES
                }
            )
        ),
        schema_version=len(load_migrations()),
        epoch=epoch,
        startup_deadline_seconds=1,
        _env_file=None,
    )


@pytest.mark.parametrize("role", PROCESS_WORKLOADS)
async def test_process_admits_own_login_and_observes_maintenance(environment, role):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="c" * 40)
    epoch = uuid4()
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false",
        (epoch,),
    )
    database = await open_process_metadata(role, settings=runtime_settings(environment, epoch))
    try:
        assert database is not None and metadata_db() is database
        assert await check_metadata(database, role) == "read_write"
        await environment.admin.execute("UPDATE nova_meta.deployment_state SET maintenance=true")
        assert await check_metadata(database, role) == "read_only"
    finally:
        await close_process_metadata(database, role)


async def test_unpublished_epoch_refuses_before_pool_installation(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="c" * 40)
    with pytest.raises(MetadataSchemaMismatch):
        await open_process_metadata("web", settings=runtime_settings(environment, uuid4()))
    from app.core.metadata_db import MetadataUnavailable

    with pytest.raises(MetadataUnavailable):
        metadata_db()
