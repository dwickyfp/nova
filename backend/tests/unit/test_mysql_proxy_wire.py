"""Wire-level behaviour of streamed responses and large packets."""

import asyncio
import struct

from app.proxy import protocol as p
from app.proxy.connection import ProxyConnection, WireSink
from app.proxy.executor import WireResult


class FakeWriter:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        return None

    def get_extra_info(self, name):
        return None


def _connection(capabilities: int, reader: asyncio.StreamReader | None = None):
    connection = ProxyConnection(reader or asyncio.StreamReader(), FakeWriter(), connection_id=7)
    connection._ctx.capabilities = capabilities
    return connection


def _packets(data: bytes) -> list[tuple[int, bytes]]:
    packets, offset = [], 0
    while offset < len(data):
        length = int.from_bytes(data[offset : offset + 3], "little")
        packets.append((data[offset + 3], bytes(data[offset + 4 : offset + 4 + length])))
        offset += 4 + length
    return packets


def test_small_payload_is_one_frame():
    frames, next_sequence = p.frame_payload(b"abc", 4)
    assert frames == b"\x03\x00\x00\x04abc" and next_sequence == 5


def test_payload_of_exactly_one_frame_ends_with_an_empty_frame():
    payload = b"x" * p.MAX_FRAME_PAYLOAD
    frames, next_sequence = p.frame_payload(payload, 1)
    packets = _packets(frames)
    assert [len(body) for _, body in packets] == [p.MAX_FRAME_PAYLOAD, 0]
    assert [sequence for sequence, _ in packets] == [1, 2] and next_sequence == 3


async def test_continuation_frames_are_reassembled():
    reader = asyncio.StreamReader()
    first = b"\x03" + b"s" * (p.MAX_FRAME_PAYLOAD - 1)
    reader.feed_data(b"\xff\xff\xff\x00" + first + b"\x02\x00\x00\x01;!")
    connection = _connection(p.SERVER_CAPABILITIES, reader)

    payload = await connection._read_packet()

    assert payload == first + b";!"
    assert connection._response_sequence == 2


async def test_multi_result_responses_carry_more_results_until_the_last():
    connection = _connection(p.SERVER_CAPABILITIES)
    sink = WireSink(connection)
    columns = [p.ColumnDefinition(name="v")]

    await sink.emit(WireResult(ok_affected=1), more=True)
    await sink.columns(columns, more=False)
    await sink.rows([[1], [2]], columns)
    await sink.end_rows(more=False)

    packets = _packets(connection._writer.data)
    assert [sequence for sequence, _ in packets] == list(range(1, len(packets) + 1))
    ok_status = struct.unpack("<H", packets[0][1][3:5])[0]
    final_status = struct.unpack("<H", packets[-1][1][3:5])[0]
    assert ok_status & p.SERVER_MORE_RESULTS_EXISTS
    assert not final_status & p.SERVER_MORE_RESULTS_EXISTS
    assert sink.complete and not sink.errored


async def test_client_without_multi_results_gets_only_the_final_response():
    connection = _connection(p.SERVER_CAPABILITIES & ~p.CLIENT_MULTI_RESULTS)
    sink = WireSink(connection)
    columns = [p.ColumnDefinition(name="v")]

    await sink.columns(columns, more=True)
    await sink.rows([[1]], columns)
    await sink.end_rows(more=True)
    await sink.emit(WireResult(ok_affected=3), more=False)

    packets = _packets(connection._writer.data)
    assert len(packets) == 1 and packets[0][1][0] == 0x00 and packets[0][0] == 1


async def test_error_after_rows_ends_the_command():
    connection = _connection(p.SERVER_CAPABILITIES)
    sink = WireSink(connection)
    columns = [p.ColumnDefinition(name="v")]

    await sink.columns(columns, more=False)
    await sink.rows([[1]], columns)
    await sink.emit(WireResult(error="boom", error_code=1317), more=False)

    packets = _packets(connection._writer.data)
    assert packets[-1][1][0] == 0xFF
    assert sink.errored and sink.complete


async def test_large_row_is_split_into_frames():
    connection = _connection(p.SERVER_CAPABILITIES)
    sink = WireSink(connection)
    columns = [p.ColumnDefinition(name="v")]
    value = "j" * (p.MAX_FRAME_PAYLOAD + 10)

    await sink.columns(columns, more=False)
    await sink.rows([[value]], columns)
    await sink.end_rows(more=False)

    packets = _packets(connection._writer.data)
    assert [sequence for sequence, _ in packets] == list(range(1, len(packets) + 1))
    row = b"".join(body for _, body in packets[3:-1])
    assert row.endswith(value.encode()[-100:])


def _execute_payload(statement_id, params):
    """Build a COM_STMT_EXECUTE body the way a driver does."""
    body = struct.pack("<I", statement_id) + b"\x00" + struct.pack("<I", 1)
    bitmap = bytearray((len(params) + 7) // 8)
    types, values = b"", b""
    for index, (type_code, raw) in enumerate(params):
        if raw is None:
            bitmap[index // 8] |= 1 << (index % 8)
        else:
            values += raw
        types += bytes([type_code, 0])
    return body + bytes(bitmap) + b"\x01" + types + values


def test_execute_parameters_decode_by_type():
    import datetime
    from decimal import Decimal

    payload = _execute_payload(
        5,
        [
            (p.TYPE_LONGLONG, struct.pack("<q", -7)),
            (p.TYPE_DOUBLE, struct.pack("<d", 2.5)),
            (p.TYPE_VAR_STRING, p.encode_length_encoded_str(b"it's")),
            (p.TYPE_NEWDECIMAL, p.encode_length_encoded_str(b"12.34")),
            (p.TYPE_DATETIME, bytes([7]) + struct.pack("<H", 2024) + bytes([2, 29, 1, 2, 3])),
            (p.TYPE_DATE, bytes([4]) + struct.pack("<H", 2024) + bytes([1, 2])),
            (p.TYPE_NULL, None),
        ],
    )
    request = p.parse_stmt_execute(payload, num_params=7, previous_types=None)
    assert request.statement_id == 5
    assert request.values == [
        -7,
        2.5,
        "it's",
        Decimal("12.34"),
        datetime.datetime(2024, 2, 29, 1, 2, 3),
        datetime.date(2024, 1, 2),
        None,
    ]


def test_long_data_replaces_the_parameter_value():
    payload = struct.pack("<I", 1) + b"\x00" + struct.pack("<I", 1) + b"\x00\x01"
    payload += bytes([p.TYPE_BLOB, 0])
    request = p.parse_stmt_execute(
        payload, num_params=1, previous_types=None, long_data={0: b"streamed text"}
    )
    assert request.values == ["streamed text"]


def test_binary_row_encodes_nulls_and_types():
    import datetime

    columns = [
        p.ColumnDefinition(name="i", type_code=p.TYPE_LONG),
        p.ColumnDefinition(name="n", type_code=p.TYPE_VAR_STRING),
        p.ColumnDefinition(name="d", type_code=p.TYPE_DATE),
        p.ColumnDefinition(name="s", type_code=p.TYPE_VAR_STRING),
    ]
    row = p.build_binary_row([70000, None, datetime.date(2024, 2, 29), "ok"], columns)
    assert row[0] == 0x00
    assert row[1] == 1 << 3  # column 1 is NULL, at bit offset 2 + 1
    assert row[2:6] == struct.pack("<i", 70000)
    assert row[6:11] == bytes([4]) + struct.pack("<H", 2024) + bytes([2, 29])
    assert row[11:] == b"\x02ok"


def test_prepare_ok_announces_parameters():
    payloads = p.build_prepare_ok(9, 2, capabilities=p.SERVER_CAPABILITIES)
    assert payloads[0][0] == 0x00
    assert struct.unpack("<I", payloads[0][1:5])[0] == 9
    assert struct.unpack("<H", payloads[0][7:9])[0] == 2
    assert len(payloads) == 1 + 2 + 1
