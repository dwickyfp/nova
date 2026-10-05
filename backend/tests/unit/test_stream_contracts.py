from dataclasses import replace

import pytest

from app.modules.streams.schemas import (
    ChangeCapabilities,
    ChangeCursor,
    ChangeSnapshot,
    Coverage,
    StreamError,
    StreamMode,
    append_row_id,
)


def test_cursor_roundtrip_and_epoch_boundary():
    cursor = ChangeCursor(5, 100)
    assert ChangeCursor.from_token(cursor.token()) == cursor
    assert cursor.precedes(ChangeCursor(5, 101))
    with pytest.raises(StreamError, match="epoch"):
        cursor.precedes(ChangeCursor(6, 101))


@pytest.mark.parametrize("token", ["", "!", "a" * 129, "WzIsMSwyXQ", "WzEsLTEsMl0"])
def test_cursor_token_rejects_invalid_values(token):
    with pytest.raises(StreamError):
        ChangeCursor.from_token(token)


def test_snapshot_interval_is_not_cross_epoch_or_reversed():
    with pytest.raises(StreamError):
        ChangeSnapshot("source", ChangeCursor(1, 1), ChangeCursor(2, 2), 1)
    with pytest.raises(StreamError):
        ChangeSnapshot("source", ChangeCursor(1, 2), ChangeCursor(1, 1), 1)
    assert ChangeSnapshot("source", ChangeCursor(1, 1), ChangeCursor(1, 1), 1).empty


def test_managed_append_is_not_full_cdc():
    caps = ChangeCapabilities(Coverage.MANAGED_APPEND, True, True, True)
    caps.require(StreamMode.APPEND_ONLY)
    with pytest.raises(StreamError) as error:
        caps.require(StreamMode.STANDARD)
    assert error.value.code == "STREAM_MODE_UNSUPPORTED_BY_SOURCE"
    replace(caps, coverage=Coverage.FULL_CDC, before_image=True).require(StreamMode.STANDARD)


@pytest.mark.parametrize("changes", [
    {"coverage": Coverage.NOVA_WRITES_ONLY}, {"coverage": Coverage.UNAVAILABLE},
    {"write_fence": False}, {"schema_compatible": False}, {"governance_supported": False},
])
def test_incomplete_coverage_never_admits_stream(changes):
    caps = ChangeCapabilities(Coverage.MANAGED_APPEND, True, True, True)
    with pytest.raises(StreamError):
        replace(caps, **changes).require(StreamMode.APPEND_ONLY)


def test_append_row_identity_is_stable_and_batch_specific():
    assert append_row_id("s", "b", 0) == append_row_id("s", "b", 0)
    assert len({append_row_id("s", "b", n) for n in range(100)}) == 100
    assert append_row_id("s", "b", 0) != append_row_id("s", "c", 0)
