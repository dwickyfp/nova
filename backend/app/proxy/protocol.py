"""MySQL wire-protocol codec — packet framing and the packets Nova emits.

This module is deliberately engine-free and socket-free: every function takes
and returns ``bytes``. That is what lets the proxy's protocol layer be tested
without StarRocks, without MinIO and without a network — see
``tests/unit/test_mysql_proxy_protocol.py``.

Scope is exactly the subset the proxy needs:

* framing (3-byte length + sequence id),
* the server handshake and the ``mysql_native_password`` auth exchange,
* OK / ERR / EOF / resultset responses,
* the client command packets Nova must understand (handshake response,
  ``COM_QUERY``, ``COM_INIT_DB``, ``COM_QUIT``, ``COM_PING``).

Capability negotiation is a first-class concern here rather than a constant:
the client and the proxy must agree on, at minimum, ``CLIENT_CONNECT_WITH_DB``
and ``CLIENT_DEPRECATE_EOF`` *before* either side relies on them. The proxy
therefore advertises only what it implements and intersects that with what the
client asks for; a client that never sets ``DEPRECATE_EOF`` still gets the
classic EOF-delimited result set, and one that sets ``CONNECT_WITH_DB`` gets a
``schema`` field in its handshake response that is read back out.

Reference: https://dev.mysql.com/doc/dev/mysql-server/latest/page_protocol_basics.html
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

# ── Capability flags ──────────────────────────────────────────────────────

CLIENT_LONG_PASSWORD = 1 << 0
CLIENT_FOUND_ROWS = 1 << 1
CLIENT_LONG_FLAG = 1 << 2
CLIENT_CONNECT_WITH_DB = 1 << 3
CLIENT_NO_SCHEMA = 1 << 4
CLIENT_COMPRESS = 1 << 5
CLIENT_ODBC = 1 << 6
CLIENT_LOCAL_FILES = 1 << 7
CLIENT_IGNORE_SPACE = 1 << 8
CLIENT_PROTOCOL_41 = 1 << 9
CLIENT_INTERACTIVE = 1 << 10
CLIENT_SSL = 1 << 11
CLIENT_TRANSACTIONS = 1 << 13
CLIENT_SECURE_CONNECTION = 1 << 15
CLIENT_MULTI_STATEMENTS = 1 << 16
CLIENT_MULTI_RESULTS = 1 << 17
CLIENT_PS_MULTI_RESULTS = 1 << 18
CLIENT_PLUGIN_AUTH = 1 << 19
CLIENT_CONNECT_ATTRS = 1 << 20
CLIENT_PLUGIN_AUTH_LENENC_CLIENT_DATA = 1 << 21
CLIENT_DEPRECATE_EOF = 1 << 24

#: What Nova advertises in its handshake.
#:
#: Every bit here is a claim the proxy actually honours, and the set mirrors
#: what StarRocks itself advertises (measured: ``0x38820c``) rather than what is
#: theoretically supportable. Two exclusions carry real weight:
#:
#: * ``CLIENT_DEPRECATE_EOF`` is **not** advertised. Advertising it makes the
#:   server responsible for OK-terminated result sets, and StarRocks terminates
#:   with classic EOF packets. A client that negotiated the flag and then
#:   received EOF reads it as a protocol violation and drops the connection —
#:   which is exactly what the ``mysql`` 8.0.46 CLI did against an earlier
#:   revision of this file. Not offering the capability keeps both sides in EOF
#:   mode, which is the mode the engine is in.
#: * ``CLIENT_SSL`` is **not** advertised: the proxy speaks plaintext and must
#:   not let a client believe it can upgrade mid-handshake.
SERVER_CAPABILITIES = (
    CLIENT_LONG_PASSWORD
    | CLIENT_FOUND_ROWS
    | CLIENT_LONG_FLAG
    | CLIENT_CONNECT_WITH_DB
    | CLIENT_PROTOCOL_41
    | CLIENT_TRANSACTIONS
    | CLIENT_SECURE_CONNECTION
    | CLIENT_MULTI_STATEMENTS
    | CLIENT_MULTI_RESULTS
    | CLIENT_PS_MULTI_RESULTS
    | CLIENT_PLUGIN_AUTH
    | CLIENT_CONNECT_ATTRS
    | CLIENT_PLUGIN_AUTH_LENENC_CLIENT_DATA
)

SERVER_VERSION = "8.0.33-Nova"

#: Charset id 45 = utf8mb4_general_ci.
CHARSET_UTF8MB4 = 45

#: Character set used for string columns the proxy itself synthesises (for
#: example the ``Database`` column of a ``SHOW DATABASES`` rewrite). 33 is
#: utf8_general_ci, which every client can decode.
CHARSET_UTF8 = 33

SERVER_STATUS_AUTOCOMMIT = 1 << 1

#: Status flags the proxy reports.
#:
#: ``SERVER_STATUS_NO_BACKSLASH_ESCAPES`` would tell the client that ``\`` is an
#: ordinary character in string literals, and the client then stops escaping it.
#: StarRocks itself reports only autocommit (measured: its post-login OK packet
#: carries status ``0x0000``, i.e. not even the autocommit bit), so advertising
#: the no-backslash flag would describe a server the client is not talking to.
#: Autocommit alone is what both StarRocks and MySQL report on a healthy
#: connection and it is the only flag the proxy depends on.
DEFAULT_SERVER_STATUS = SERVER_STATUS_AUTOCOMMIT

# ── Command bytes ─────────────────────────────────────────────────────────

COM_QUIT = 0x01
COM_INIT_DB = 0x02
COM_QUERY = 0x03
COM_FIELD_LIST = 0x04
COM_PING = 0x0E
COM_CHANGE_USER = 0x11
COM_STMT_PREPARE = 0x16
COM_STMT_EXECUTE = 0x17
COM_STMT_CLOSE = 0x19
COM_STMT_RESET = 0x1A
COM_SET_OPTION = 0x1B

# ── Field types and flags ─────────────────────────────────────────────────

TYPE_TINY = 0x01
TYPE_SHORT = 0x02
TYPE_LONG = 0x03
TYPE_FLOAT = 0x04
TYPE_DOUBLE = 0x05
TYPE_NULL = 0x06
TYPE_TIMESTAMP = 0x07
TYPE_LONGLONG = 0x08
TYPE_INT24 = 0x09
TYPE_DATE = 0x0A
TYPE_TIME = 0x0B
TYPE_DATETIME = 0x0C
TYPE_YEAR = 0x0D
TYPE_VARCHAR = 0x0F
TYPE_BIT = 0x10
TYPE_JSON = 0xF5
TYPE_NEWDECIMAL = 0xF6
TYPE_BLOB = 0xFC
TYPE_VAR_STRING = 0xFD
TYPE_STRING = 0xFE

NOT_NULL_FLAG = 1 << 0
UNSIGNED_FLAG = 1 << 5
BINARY_FLAG = 1 << 7

# ── MySQL error codes ─────────────────────────────────────────────────────

ER_ACCESS_DENIED_ERROR = 1045
ER_NO_DB_ERROR = 1046
ER_UNKNOWN_COM_ERROR = 1047
ER_PARSE_ERROR = 1064
ER_NOT_SUPPORTED_YET = 1235

#: SQLSTATE paired with every error the proxy produces. ``HY000`` is the
#: general error state; ``42000`` is the syntax/access state MySQL uses for
#: 1064 and 1045-parsed statements.
SQLSTATE_GENERAL = "HY000"
SQLSTATE_SYNTAX = "42000"

#: Hard ceiling on a single framed packet. MySQL frames payloads larger than
#: 0xFFFFFF by splitting them; a length of exactly 0xFFFFFF means "more
#: follows". The proxy refuses anything above 16 MiB rather than reading an
#: unbounded amount into memory on the say-so of a peer.
MAX_PACKET_SIZE = 16 * 1024 * 1024


class ProtocolError(Exception):
    """A malformed or unsupported packet arrived from the client."""


# ── Length-encoded integers / strings ─────────────────────────────────────


def encode_length_encoded_int(value: int) -> bytes:
    """Encode ``value`` as a MySQL length-encoded integer.

    ``1`` is one byte; the OK packet's ``affected_rows`` is a length-encoded
    integer and not a single byte, so row counts above 250 are representable.
    """
    if value < 0:
        raise ValueError("length-encoded integers are unsigned")
    if value < 0xFB:
        return struct.pack("<B", value)
    if value < 1 << 16:
        return b"\xfc" + struct.pack("<H", value)
    if value < 1 << 24:
        return b"\xfd" + struct.pack("<I", value)[:3]
    if value < 1 << 64:
        return b"\xfe" + struct.pack("<Q", value)
    raise ValueError("value too large for a length-encoded integer")


def encode_length_encoded_str(value: bytes) -> bytes:
    return encode_length_encoded_int(len(value)) + value


def encode_length_encoded_null() -> bytes:
    return b"\xfb"


class PacketReader:
    """Cursor over one packet payload.

    A tiny reader is worth having over ``struct.unpack_from`` calls at every
    call site because half the handshake response is null-terminated strings
    and length-encoded values; reading them in sequence is far clearer when the
    cursor is implicit.
    """

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    @property
    def remaining(self) -> int:
        return len(self._data) - self._pos

    @property
    def consumed(self) -> int:
        return self._pos

    def read(self, size: int) -> bytes:
        if self._pos + size > len(self._data):
            raise ProtocolError("packet truncated")
        chunk = self._data[self._pos : self._pos + size]
        self._pos += size
        return chunk

    def read_uint8(self) -> int:
        return self.read(1)[0]

    def read_uint16(self) -> int:
        return struct.unpack("<H", self.read(2))[0]

    def read_uint24(self) -> int:
        raw = self.read(3)
        return raw[0] | (raw[1] << 8) | (raw[2] << 16)

    def read_uint32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def read_null_terminated(self) -> bytes:
        end = self._data.find(b"\x00", self._pos)
        if end < 0:
            raise ProtocolError("unterminated string in packet")
        chunk = self._data[self._pos : end]
        self._pos = end + 1
        return chunk

    def read_length_encoded_int(self) -> int | None:
        first = self.read_uint8()
        if first < 0xFB:
            return first
        if first == 0xFB:
            return None
        if first == 0xFC:
            return self.read_uint16()
        if first == 0xFD:
            return self.read_uint24()
        if first == 0xFE:
            return struct.unpack("<Q", self.read(8))[0]
        raise ProtocolError("invalid length-encoded integer prefix")

    def read_length_encoded_bytes(self) -> bytes | None:
        size = self.read_length_encoded_int()
        if size is None:
            return None
        return self.read(size)

    def skip(self, size: int) -> None:
        self.read(size)


# ── Packet framing ────────────────────────────────────────────────────────


def encode_packet(payload: bytes, sequence_id: int) -> bytes:
    """Frame ``payload`` with a 3-byte little-endian length and sequence id."""
    if len(payload) >= 1 << 24:
        raise ValueError("packet payload exceeds the 3-byte length field")
    header = len(payload).to_bytes(3, "little") + bytes([sequence_id & 0xFF])
    return header + payload


def decode_packet(data: bytes) -> tuple[int, int, bytes]:
    """Split one framed packet into ``(sequence_id, payload_length, payload)``."""
    if len(data) < 4:
        raise ProtocolError("packet header truncated")
    length = int.from_bytes(data[:3], "little")
    sequence_id = data[3]
    payload = data[4 : 4 + length]
    if len(payload) != length:
        raise ProtocolError("packet payload truncated")
    return sequence_id, length, payload


# ── Handshake ─────────────────────────────────────────────────────────────


def generate_scramble() -> bytes:
    """20 random bytes for a ``mysql_native_password`` challenge.

    ``os.urandom`` rather than ``random``: the scramble is the only thing
    stopping an attacker from replaying a captured auth response, so it has to
    come from a CSPRNG. ``random`` is seeded predictably and is not one.
    """
    import os

    return os.urandom(20)


def scramble_native_password(password: bytes | str, scramble: bytes) -> bytes:
    """``SHA1(pwd) XOR SHA1(scramble + SHA1(SHA1(pwd)))``.

    Nova verifies credentials against StarRocks via ``verify_credentials``, so
    this function exists to *complete* the handshake, not to authenticate by
    itself: the client computes it from its password, the proxy computes it
    from the password it just proved against StarRocks, and the comparison of
    the two is what makes the exchange a real authentication rather than a
    formality.
    """
    if isinstance(password, str):
        password = password.encode("utf-8")
    if not password:
        return b""
    stage1 = hashlib.sha1(password).digest()
    stage2 = hashlib.sha1(stage1).digest()
    digest = hashlib.sha1(scramble + stage2).digest()
    return bytes(a ^ b for a, b in zip(digest, stage1, strict=False))


def build_handshake_packet(
    connection_id: int,
    scramble: bytes,
    *,
    server_version: str = SERVER_VERSION,
    capabilities: int = SERVER_CAPABILITIES,
) -> bytes:
    """Build the initial ``Protocol::HandshakeV10`` payload.

    The scramble is written in the two places the protocol puts it — bytes
    9..16 and the auth-plugin-data tail — with the 20-byte total the plugin
    expects. Writing it in only one place is the classic bug: the client then
    derives a response from a salt the server never used.
    """
    if len(scramble) != 20:
        raise ValueError("scramble must be exactly 20 bytes")
    payload = bytearray()
    payload += b"\x0a"  # protocol version 10
    payload += server_version.encode("ascii") + b"\x00"
    payload += struct.pack("<I", connection_id)
    payload += scramble[:8]
    payload += b"\x00"  # filler
    payload += struct.pack("<H", capabilities & 0xFFFF)
    payload += struct.pack("<B", CHARSET_UTF8MB4)
    payload += struct.pack("<H", DEFAULT_SERVER_STATUS)
    payload += struct.pack("<H", (capabilities >> 16) & 0xFFFF)
    payload += struct.pack("<B", 21)  # auth plugin data length (20 + NUL)
    payload += b"\x00" * 10  # reserved
    payload += scramble[8:] + b"\x00"
    payload += b"mysql_native_password\x00"
    return bytes(payload)


def build_auth_switch_request(plugin_name: bytes, scramble: bytes) -> bytes:
    return b"\xfe" + plugin_name + b"\x00" + scramble + b"\x00"


@dataclass
class HandshakeResponse:
    """The parsed subset of a client ``HandshakeResponse41``."""

    capabilities: int
    max_packet_size: int
    charset: int
    username: str
    auth_response: bytes
    database: str | None = None
    auth_plugin: str | None = None
    connect_attrs: dict[str, str] = field(default_factory=dict)


def parse_handshake_response(payload: bytes) -> HandshakeResponse:
    """Parse a client handshake response.

    Fields are read strictly in wire order and each one is gated on the
    capability bit that makes it present. Skipping that gating is how a parser
    ends up reading the auth response as the database name: ``CONNECT_WITH_DB``
    and ``PLUGIN_AUTH`` change the layout, and both are negotiable.
    """
    reader = PacketReader(payload)
    capabilities = reader.read_uint32()
    max_packet_size = reader.read_uint32()
    charset = reader.read_uint8()
    reader.skip(23)  # reserved

    if not capabilities & CLIENT_PROTOCOL_41:
        raise ProtocolError("client does not speak Protocol::HandshakeResponse41")

    username = reader.read_null_terminated().decode("utf-8", errors="surrogateescape")

    if capabilities & CLIENT_PLUGIN_AUTH_LENENC_CLIENT_DATA:
        auth_response = reader.read_length_encoded_bytes() or b""
    elif capabilities & CLIENT_SECURE_CONNECTION:
        auth_response = reader.read(reader.read_uint8())
    else:
        auth_response = reader.read_null_terminated()

    database = None
    if capabilities & CLIENT_CONNECT_WITH_DB and reader.remaining:
        database = reader.read_null_terminated().decode("utf-8", errors="surrogateescape")
        if not database:
            database = None

    auth_plugin = None
    if capabilities & CLIENT_PLUGIN_AUTH and reader.remaining:
        plugin_raw = reader.read_null_terminated()
        if plugin_raw:
            auth_plugin = plugin_raw.decode("ascii", errors="replace")

    connect_attrs: dict[str, str] = {}
    if capabilities & CLIENT_CONNECT_ATTRS and reader.remaining:
        # The attrs blob is length-encoded as a whole and then as
        # key/value pairs. A client that sends a malformed blob should not
        # take the connection down, so parse it defensively: attrs are
        # decoration, never auth material.
        try:
            blob_length = reader.read_length_encoded_int() or 0
            blob = reader.read(blob_length) if blob_length else b""
            attr_reader = PacketReader(blob)
            while attr_reader.remaining > 0:
                key = attr_reader.read_length_encoded_bytes()
                if key is None:
                    break
                value = attr_reader.read_length_encoded_bytes()
                if value is None:
                    break
                connect_attrs[key.decode("utf-8", errors="replace")] = value.decode(
                    "utf-8", errors="replace"
                )
        except ProtocolError:
            connect_attrs = {}

    return HandshakeResponse(
        capabilities=capabilities,
        max_packet_size=max_packet_size,
        charset=charset,
        username=username,
        auth_response=auth_response,
        database=database,
        auth_plugin=auth_plugin,
        connect_attrs=connect_attrs,
    )


# ── OK / ERR / EOF ────────────────────────────────────────────────────────


#: Capability set used when a caller does not name one. This is the *baseline*
#: Protocol 4.1 client, not ``SERVER_CAPABILITIES``: a default of
#: ``SERVER_CAPABILITIES`` would silently opt every caller into
#: ``CLIENT_DEPRECATE_EOF`` and produce ``OK``-terminated result sets for
#: clients that never agreed to them. Anything that depends on the negotiated
#: set must be told the negotiated set — see ``build_resultset``.
DEFAULT_PROTOCOL_CAPABILITIES = CLIENT_PROTOCOL_41


def build_ok_packet(
    affected_rows: int = 0,
    last_insert_id: int = 0,
    *,
    status_flags: int = DEFAULT_SERVER_STATUS,
    warnings: int = 0,
    message: bytes = b"",
    capabilities: int = DEFAULT_PROTOCOL_CAPABILITIES,
) -> bytes:
    """Build an ``OK_Packet``.

    ``affected_rows`` and ``last_insert_id`` are length-encoded integers, not
    fixed-width fields — a 300-row DML statement is three bytes here, and
    writing it as one byte silently reports 44.

    The ``0x00`` header and the trailing status/warning words are Protocol 4.1
    features, and the proxy only ever speaks 4.1 (``parse_handshake_response``
    refuses anything else). They are therefore emitted unconditionally rather
    than keyed off ``capabilities``: ``CLIENT_DEPRECATE_EOF`` and
    ``CLIENT_PROTOCOL_41`` are independent bits, and keying the header off the
    caller's mask meant a caller that passed only ``DEPRECATE_EOF`` got the
    pre-4.1 ``0xFE`` header — a packet that reads as an EOF and desynchronises
    the client.
    """
    del capabilities
    payload = b"\x00"
    payload += encode_length_encoded_int(affected_rows)
    payload += encode_length_encoded_int(last_insert_id)
    payload += struct.pack("<HH", status_flags, warnings)
    payload += message
    return payload


def build_error_packet(
    code: int,
    message: str,
    *,
    sqlstate: str = SQLSTATE_GENERAL,
    capabilities: int = DEFAULT_PROTOCOL_CAPABILITIES,
) -> bytes:
    """Build an ``ERR_Packet``.

    The message is scrubbed of newlines and collapsed to one line: a Python
    traceback tail pasted into a client's terminal is unreadable, and worse, it
    can carry engine internals. One line, no traceback — the detail belongs in
    the server log, not on the wire.
    """
    clean = " ".join(message.split())
    payload = b"\xff" + struct.pack("<H", code)
    if capabilities & CLIENT_PROTOCOL_41:
        payload += b"#" + sqlstate.encode("ascii")[:5].ljust(5, b"0")
    payload += clean.encode("utf-8", errors="replace")
    return payload


def build_eof_packet(
    *,
    status_flags: int = DEFAULT_SERVER_STATUS,
    warnings: int = 0,
) -> bytes:
    return b"\xfe" + struct.pack("<HH", warnings, status_flags)


# ── Result sets ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ColumnDefinition:
    """A column the proxy knows how to describe on the wire."""

    name: str
    type_code: int = TYPE_VAR_STRING
    charset: int = CHARSET_UTF8
    column_length: int = 1024
    flags: int = 0
    decimals: int = 0


def build_column_definition(col: ColumnDefinition) -> bytes:
    """Encode a ``Protocol::ColumnDefinition41`` payload."""

    def _lenenc(value: bytes) -> bytes:
        return encode_length_encoded_str(value)

    name = col.name.encode("utf-8")
    payload = bytearray()
    payload += _lenenc(b"def")  # catalog
    payload += _lenenc(b"")  # schema
    payload += _lenenc(b"")  # table
    payload += _lenenc(b"")  # org_table
    payload += _lenenc(name)  # name
    payload += _lenenc(name)  # org_name
    payload += b"\x0c"  # fixed-length field count
    payload += struct.pack("<H", col.charset)
    payload += struct.pack("<I", col.column_length)
    payload += struct.pack("<B", col.type_code)
    payload += struct.pack("<H", col.flags)
    payload += struct.pack("<B", col.decimals)
    payload += b"\x00\x00"  # filler
    return bytes(payload)


def _is_binary_type(type_code: int) -> bool:
    return type_code in (TYPE_BLOB, TYPE_BIT)


def build_text_row(values: list, columns: list[ColumnDefinition]) -> bytes:
    """Encode one row of a text protocol result set.

    ``None`` is the ``0xFB`` NULL marker; text-format values are the UTF-8
    rendering of the Python value. Non-UTF-8 bytes (a BLOB read as text) are
    passed through with ``surrogateescape`` rather than raising, because losing
    a row is worse than transporting a byte the client will decode its own way.

    ``columns`` is accepted (and may be shorter than ``values``) so the caller
    can reuse one description list across rows; the text protocol carries no
    per-row type information, so nothing in the row's encoding depends on it
    today. The parameter is kept because the binary protocol does.
    """
    del columns
    payload = bytearray()
    for value in values:
        if value is None:
            payload += encode_length_encoded_null()
            continue
        if isinstance(value, bytes):
            encoded = value
        elif isinstance(value, bool):
            encoded = b"1" if value else b"0"
        else:
            encoded = str(value).encode("utf-8", errors="surrogateescape")
        payload += encode_length_encoded_int(len(encoded))
        payload += encoded
    return bytes(payload)


def build_resultset(
    columns: list[ColumnDefinition],
    rows: list[list],
    *,
    capabilities: int = DEFAULT_PROTOCOL_CAPABILITIES,
) -> list[bytes]:
    """Build the framed packets of a complete text result set.

    Returns *unframed payloads* paired with their sequence id by the caller;
    keeping framing out of here means the codec stays pure and the unit tests
    can assert on payload bytes directly.

    The two layouts the proxy must support:

    * ``CLIENT_DEPRECATE_EOF`` negotiated → the metadata block is *not* closed
      by an EOF packet, and the row block is terminated by an ``OK`` packet
      carrying the status flags.
    * otherwise → an ``EOF`` packet closes the metadata block and another
      closes the rows.

    Getting this branch wrong is the usual source of "MySQL server has gone
    away" immediately after connect: the client counts packets and desynchronises.
    """
    payloads: list[bytes] = [encode_length_encoded_int(len(columns))]
    payloads.extend(build_column_definition(column) for column in columns)

    if not capabilities & CLIENT_DEPRECATE_EOF:
        payloads.append(build_eof_packet())

    for row in rows:
        payloads.append(build_text_row(list(row), columns))

    if capabilities & CLIENT_DEPRECATE_EOF:
        payloads.append(build_ok_packet(0, 0, capabilities=capabilities))
    else:
        payloads.append(build_eof_packet())

    return payloads
