"""Stage registrations for unit tests; real PG tests cover grants and scope cleanup."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.modules.stages import metadata_service as module


def install_stages(monkeypatch, rows):
    async def identities(*, identity=None, name=None, database=None, schema=None):
        return [row for row in rows
                if (identity is None or row["id"] == identity)
                and (name is None or row["name"] == name)
                and (database is None or row["database_name"] == database)
                and (schema is None or row["schema_name"] == schema)]

    async def get(identity):
        return next((row for row in rows if row["id"] == identity), None)

    @asynccontextmanager
    async def authorized(self, stage, action):
        await (self._access_check or module.check_stage_access)(
            stage, action=action, username=self.actor.principal,
            active_role=self.actor.active_role,
        )
        yield

    monkeypatch.setattr(module, "StageRepository", lambda connection: SimpleNamespace(
        identities=identities, get=get, in_scope=AsyncMock(return_value=rows),
    ))
    monkeypatch.setattr(module.StageMetadataService, "_scope", authorized)
    monkeypatch.setattr(module.StageMetadataService, "_bound_actor", AsyncMock())
    return rows
