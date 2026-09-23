from unittest.mock import AsyncMock

import pytest

from app.modules.stages import router


@pytest.mark.asyncio
async def test_stage_mutation_records_attempt_and_outcome(monkeypatch):
    audit = AsyncMock()
    monkeypatch.setattr(router, "write_audit_log", audit)
    stage = {"database_name": "db", "schema_name": "bronze"}
    user = {"username": "analyst", "active_role": "loader"}

    async with router._stage_mutation_audit(stage, user, "UPLOAD_STAGE_FILE", "stage/file.csv"):
        assert audit.call_count == 1
        assert audit.call_args.kwargs["status"] == "ATTEMPTED"

    assert audit.call_count == 2
    assert audit.call_args.kwargs["status"] == "SUCCESS"
    assert audit.call_args.kwargs["database_name"] == "db"


@pytest.mark.asyncio
async def test_stage_mutation_fails_closed_and_redacts_error(monkeypatch):
    audit = AsyncMock()
    monkeypatch.setattr(router, "write_audit_log", audit)
    stage = {"database_name": "db", "schema_name": "bronze"}
    user = {"username": "analyst"}

    with pytest.raises(ValueError, match="credential-in-error"):
        async with router._stage_mutation_audit(stage, user, "DELETE_STAGE", "stage"):
            raise ValueError("credential-in-error")

    assert audit.call_args.kwargs["status"] == "ERROR"
    assert audit.call_args.kwargs["error_message"] == "ValueError"

    audit.side_effect = RuntimeError("audit unavailable")
    mutated = False
    with pytest.raises(RuntimeError, match="audit unavailable"):
        async with router._stage_mutation_audit(stage, user, "DELETE_STAGE", "stage"):
            mutated = True
    assert not mutated


@pytest.mark.asyncio
async def test_stage_audit_bounds_long_object_names(monkeypatch):
    audit = AsyncMock()
    monkeypatch.setattr(router, "write_audit_log", audit)

    async with router._stage_mutation_audit(
        {"database_name": "db", "schema_name": "bronze"},
        {"username": "analyst"},
        "UPLOAD_STAGE_FILE",
        "stage/" + "あ" * 300,
    ):
        pass

    name = audit.call_args.kwargs["object_name"]
    assert len(name.encode("utf-8")) <= 512
    assert "#" in name
