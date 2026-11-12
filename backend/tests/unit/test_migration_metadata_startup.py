"""Ingestion startup cannot recreate or fall back to retired engine metadata."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.metadata_db import MetadataUnavailable
from app.modules.migration.repository import MigrationRepository


@pytest.mark.parametrize("unavailable", [False, True])
async def test_startup_probes_pg_and_never_opens_legacy_metadata(monkeypatch, unavailable):
    from app.core import metadata_runtime

    probe = AsyncMock(side_effect=MetadataUnavailable() if unavailable else None)
    monkeypatch.setattr(metadata_runtime, "metadata_db", lambda: SimpleNamespace(probe=probe))
    engine = AsyncMock(side_effect=AssertionError("Startup must not open StarRocks metadata"))
    monkeypatch.setattr(MigrationRepository, "_connect", engine)
    if unavailable:
        with pytest.raises(MetadataUnavailable):
            await MigrationRepository().ensure_schema()
    else:
        await MigrationRepository().ensure_schema()
    probe.assert_awaited_once()
    engine.assert_not_awaited()
