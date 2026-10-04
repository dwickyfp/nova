import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import asyncmy
import pytest
from asyncmy.constants import FIELD_TYPE

from app.modules.query.repository import (
    QueryRepository,
    _binary_result_mode,
    _decode_result_rows,
)


async def test_binary_result_mode_preserves_numeric_temporal_and_binary_converters():
    conn = asyncmy.Connection()
    decoders = conn._decoders
    try:
        with _binary_result_mode(conn, True) as enabled:
            assert enabled and conn._use_unicode is False
            for code, raw, expected in [
                (FIELD_TYPE.LONG, b"42", 42),
                (FIELD_TYPE.DOUBLE, b"1.25", 1.25),
                (FIELD_TYPE.NEWDECIMAL, b"12.3400", Decimal("12.3400")),
                (FIELD_TYPE.DATE, b"2024-02-29", dt.date(2024, 2, 29)),
                (FIELD_TYPE.DATETIME, b"2024-02-29 12:34:56", dt.datetime(2024, 2, 29, 12, 34, 56)),
                (FIELD_TYPE.TIME, b"-25:02:03", -dt.timedelta(hours=25, minutes=2, seconds=3)),
                (FIELD_TYPE.BLOB, b"\x00\xff", b"\x00\xff"),
            ]:
                assert conn._decoders[code](raw) == expected
        assert conn._use_unicode is True and conn._decoders is decoders
        with pytest.raises(RuntimeError), _binary_result_mode(conn, True):
            raise RuntimeError("query failure")
        assert conn._use_unicode is True and conn._decoders is decoders
    finally:
        conn.close()


def test_engine_results_decode_text_and_preserve_binary_and_invalid_utf8():
    fields = [
        SimpleNamespace(type_code=code, charsetnr=charset)
        for code, charset in [
            (253, 45),
            (253, 45),
            (252, 63),
            (245, 63),
            (3, 63),
            (253, 45),
        ]
    ]
    cursor = SimpleNamespace(_result=SimpleNamespace(fields=fields))
    values = ["Jakarta 🐘".encode(), b"\x00\xff\x00", b"ASCII binary", b'{"a":1}', 42, None]
    expected = [["Jakarta 🐘", b"\x00\xff\x00", b"ASCII binary", '{"a":1}', 42, None]]
    assert _decode_result_rows(cursor, [tuple(values)]) == expected
    assert _decode_result_rows(cursor, [dict(zip("abcdef", values, strict=True))]) == expected


async def test_relay_result_mode_restores_connection_after_unbuffered_cursor_drains():
    conn = asyncmy.Connection()
    decoders = conn._decoders
    events = []

    class Cursor:
        description = [("geometry", 253, None, 4096, 4096, 0, True)]
        _result = SimpleNamespace(fields=[SimpleNamespace(type_code=253, charsetnr=45)])

        async def __aenter__(self):
            assert conn._use_unicode is False
            events.append("opened")
            return self

        async def __aexit__(self, *_):
            assert conn._use_unicode is False
            events.append("drained")

        async def execute(self, sql):
            assert sql == "SELECT geometry FROM locations"

        async def fetchmany(self, count):
            assert count == 2
            return [{"geometry": b"\x00\xff"}, {"geometry": b"\x00\xfe"}]

    conn.cursor = lambda _: Cursor()
    try:
        result = await QueryRepository._execute_on(
            conn,
            "SELECT geometry FROM locations",
            role=None,
            max_rows=1,
            start=0.0,
            binary_results=True,
        )
        assert result.rows == [[b"\x00\xff"]] and result.truncated
        assert events == ["opened", "drained"]
        assert conn._use_unicode is True and conn._decoders is decoders
    finally:
        conn.close()
