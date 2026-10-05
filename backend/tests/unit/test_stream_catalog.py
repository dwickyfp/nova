from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.modules.streams.catalog import SourceHead, StreamCatalog
from app.modules.streams.claims import ClaimOutcome, ClaimResult
from app.modules.streams.namespace import StreamName
from app.modules.streams.repository import StreamRecord, StreamRepository
from app.modules.streams.schemas import ChangeCursor, StreamError


@pytest.fixture
def catalog():
    repo = AsyncMock()
    repo.current.return_value = None
    repo.publish.side_effect = lambda record: record
    access = AsyncMock()
    access.collision.return_value = False
    access.source.return_value = SourceHead("source", ChangeCursor(1, 42))
    return StreamCatalog(repo, access)


NAME = StreamName("db", "s")
SOURCE = StreamName("source_db", "t")


async def test_create_starts_at_head_and_recreation_changes_id(catalog):
    first = await catalog.create(NAME, SOURCE, "reader")
    assert first.cursor == ChangeCursor(1, 42)
    catalog.repository.current.return_value = first
    await catalog.drop(NAME)
    tombstone = catalog.repository.publish.call_args.args[0]
    assert tombstone.status == "DROPPED"
    assert tombstone.generation == 1
    catalog.repository.current.return_value = tombstone
    second = await catalog.create(NAME, SOURCE, "reader")
    assert second.generation == 2
    assert second.stream_id != first.stream_id


async def test_if_not_exists_authorizes_existing_record(catalog):
    first = await catalog.create(NAME, SOURCE, "reader")
    catalog.repository.current.return_value = first
    assert await catalog.create(NAME, SOURCE, "reader", if_not_exists=True) == first
    catalog.access.stream.assert_awaited_once_with(first)
    catalog.access.stream.side_effect = PermissionError("revoked")
    with pytest.raises(PermissionError):
        await catalog.create(NAME, SOURCE, "reader", if_not_exists=True)


@pytest.mark.parametrize("operation", ["create", "get", "drop"])
async def test_collision_always_blocks(catalog, operation):
    catalog.access.collision.return_value = True
    with pytest.raises(StreamError, match="table or view"):
        if operation == "create":
            await catalog.create(NAME, SOURCE, "reader", if_not_exists=True)
        else:
            await getattr(catalog, operation)(NAME)
    catalog.repository.current.assert_not_awaited()


async def test_revoked_namespace_does_not_read_metadata(catalog):
    catalog.access.namespace.side_effect = PermissionError("denied")
    with pytest.raises(PermissionError):
        await catalog.get(NAME)
    catalog.repository.current.assert_not_awaited()


async def test_source_denial_or_unavailable_does_not_publish(catalog):
    catalog.access.source.side_effect = PermissionError("denied")
    with pytest.raises(PermissionError):
        await catalog.create(NAME, SOURCE, "reader")
    catalog.repository.publish.assert_not_awaited()


async def test_collision_after_creation_blocks_result_and_future_use(catalog):
    catalog.access.collision.side_effect = [False, True, True]
    with pytest.raises(StreamError):
        await catalog.create(NAME, SOURCE, "reader")
    catalog.repository.publish.assert_awaited_once()
    with pytest.raises(StreamError):
        await catalog.get(NAME)


async def test_repository_lookup_uses_all_namespace_components():
    execute = AsyncMock(return_value={"rows": []})
    repo = StreamRepository(execute)
    await repo.current(NAME)
    assert execute.call_args.args[1] == NAME.key


async def test_losing_or_uncertain_claim_cannot_publish():
    execute = AsyncMock()
    repo = StreamRepository(execute)
    record = StreamRecord(NAME, 0, "id", SOURCE, "source", "reader", ChangeCursor(1, 2))
    repo.current = AsyncMock(return_value=None)
    repo.claims.acquire = AsyncMock(return_value=ClaimResult(ClaimOutcome.VERIFICATION_REQUIRED))
    with pytest.raises(StreamError, match="verification"):
        await repo.publish(record)
    execute.assert_not_awaited()


async def test_exact_readback_recovers_namespace_response_loss():
    execute = AsyncMock(side_effect=ConnectionError("lost response"))
    repo = StreamRepository(execute)
    record = StreamRecord(NAME, 0, "id", SOURCE, "source", "reader", ChangeCursor(1, 2))
    repo.current = AsyncMock(return_value=record)
    repo.claims.acquire = AsyncMock(return_value=ClaimResult(ClaimOutcome.ACQUIRED))
    assert await repo.publish(record) == record
    repo.current.return_value = replace(record, stream_id="different")
    with pytest.raises(StreamError):
        await repo.publish(record)


async def test_get_duplicate_and_drop_recheck_access(catalog):
    record = await catalog.create(NAME, SOURCE, "reader")
    catalog.repository.current.return_value = record
    catalog.repository.publish.reset_mock()
    assert await catalog.get(NAME) == record
    with pytest.raises(StreamError) as duplicate:
        await catalog.create(NAME, SOURCE, "reader")
    assert duplicate.value.code == "STREAM_ALREADY_EXISTS"
    catalog.access.stream.side_effect = PermissionError("revoked")
    for operation in (catalog.get, catalog.drop):
        with pytest.raises(PermissionError):
            await operation(NAME)
    catalog.repository.publish.assert_not_awaited()


@pytest.mark.parametrize("dropped", [False, True])
async def test_missing_and_tombstoned_streams(catalog, dropped):
    if dropped:
        catalog.repository.current.return_value = StreamRecord(
            NAME, 1, "id", SOURCE, "source", "reader", ChangeCursor(1, 2), "DROPPED",
        )
    for operation in (catalog.get, catalog.drop):
        with pytest.raises(StreamError) as error:
            await operation(NAME)
        assert error.value.code == "STREAM_NOT_FOUND"
    await catalog.drop(NAME, if_exists=True)
    catalog.repository.publish.assert_not_awaited()
