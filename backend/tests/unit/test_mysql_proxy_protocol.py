"""Unit tests for the MySQL wire-protocol codec.

No engine, no sockets, no network: every assertion here is on bytes, which is
the point of keeping ``app/proxy/protocol.py`` free of I/O. A wire-protocol bug
that only shows up against a live server is the most expensive kind to find, so
the codec is pinned directly.

The cases that matter most are the ones where a plausible-looking shortcut
produces a packet a real client rejects:

* ``affected_rows`` as a length-encoded integer, not one byte;
* the scramble written in both places the handshake puts it;
* the two result-set terminator layouts (``EOF`` vs ``CLIENT_DEPRECATE_EOF``);
* the handshake response parsed by capability bit, not by fixed offset.
"""

import struct

import pytest

from app.proxy import protocol as p


class TestLengthEncodedIntegers:
    """The encoding every variable-width field in the protocol shares."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, b"\x00"),
            (1, b"\x01"),
            (250, b"\xfa"),
            (251, b"\xfc\xfb\x00"),
            (65535, b"\xfc\xff\xff"),
            (65536, b"\xfd\x00\x00\x01"),
            (1 << 24, b"\xfe\x00\x00\x00\x01\x00\x00\x00\x00"),
        ],
    )
    def test_encode_matches_the_protocol_table(self, value, expected):
        assert p.encode_length_encoded_int(value) == expected

    @pytest.mark.parametrize("value", [0, 1, 250, 251, 65535, 65536, 16777215, 1 << 32])
    def test_round_trip(self, value):
        encoded = p.encode_length_encoded_int(value)
        assert p.PacketReader(encoded).read_length_encoded_int() == value

    def test_affected_rows_is_not_one_byte(self):
        """A 300-row DML statement must not truncate to 44.

        The design doc's sketch wrote ``affected_rows`` with ``to_bytes(1)``,
        which silently reports the wrong count for anything above 255. This is
        the regression that pins the length-encoded form.
        """
        encoded = p.encode_length_encoded_int(300)
        assert encoded == b"\xfc,\x01"
        assert p.PacketReader(encoded).read_length_encoded_int() == 300

    def test_negative_is_rejected(self):
        with pytest.raises(ValueError):
            p.encode_length_encoded_int(-1)


class TestPacketFraming:
    def test_encode_then_decode_round_trip(self):
        framed = p.encode_packet(b"hello", 3)
        assert framed[:3] == b"\x05\x00\x00"
        assert framed[3] == 3
        sequence_id, length, payload = p.decode_packet(framed)
        assert (sequence_id, length, payload) == (3, 5, b"hello")

    def test_empty_payload_is_still_framed(self):
        sequence_id, length, payload = p.decode_packet(p.encode_packet(b"", 0))
        assert (sequence_id, length, payload) == (0, 0, b"")

    def test_truncated_header_is_a_protocol_error(self):
        with pytest.raises(p.ProtocolError):
            p.decode_packet(b"\x01\x00")

    def test_truncated_payload_is_a_protocol_error(self):
        with pytest.raises(p.ProtocolError):
            p.decode_packet(b"\x10\x00\x00\x01short")

    def test_sequence_id_wraps_at_256(self):
        assert p.encode_packet(b"x", 256)[3] == 0


class TestScramble:
    def test_scramble_is_twenty_random_bytes(self):
        first = p.generate_scramble()
        second = p.generate_scramble()
        assert len(first) == 20
        assert first != second

    def test_native_password_matches_the_documented_formula(self):
        import hashlib

        password = b"hunter2"
        scramble = bytes(range(20))

        stage1 = hashlib.sha1(password).digest()
        stage2 = hashlib.sha1(stage1).digest()
        expected = bytes(
            a ^ b
            for a, b in zip(hashlib.sha1(scramble + stage2).digest(), stage1, strict=True)
        )

        assert p.scramble_native_password(password, scramble) == expected

    def test_empty_password_yields_an_empty_response(self):
        assert p.scramble_native_password(b"", bytes(range(20))) == b""

    def test_str_and_bytes_passwords_agree(self):
        scramble = p.generate_scramble()
        assert p.scramble_native_password("pw", scramble) == p.scramble_native_password(
            b"pw", scramble
        )

    def test_different_scrambles_give_different_responses(self):
        password = b"pw"
        assert p.scramble_native_password(
            password, bytes(range(20))
        ) != p.scramble_native_password(password, bytes(range(1, 21)))


class TestHandshakePacket:
    def test_carries_both_halves_of_the_scramble(self):
        """The classic handshake bug: writing only ``scramble[:8]``.

        A client derives its response from the whole 20-byte salt, so a
        handshake that publishes only the first 8 makes every login fail with a
        correct password.
        """
        scramble = bytes(range(20))
        payload = p.build_handshake_packet(7, scramble)

        assert payload[0] == 0x0A
        version_end = payload.index(b"\x00", 1)
        assert payload[1:version_end] == b"8.0.33-Nova"
        assert struct.unpack("<I", payload[version_end + 1 : version_end + 5])[0] == 7
        assert payload[version_end + 5 : version_end + 13] == scramble[:8]
        # Second half sits after the reserved bytes, before the plugin name.
        assert scramble[8:] in payload
        assert b"mysql_native_password\x00" in payload

    def test_does_not_advertise_ssl(self):
        """The proxy speaks plaintext; claiming SSL would invite an upgrade
        the proxy cannot complete."""
        capabilities = struct.unpack("<H", p.build_handshake_packet(1, bytes(20))[33:35])[0]
        assert not capabilities & p.CLIENT_SSL

    def test_does_not_advertise_deprecate_eof(self):
        """The capability the engine does not offer must not be offered either.

        Advertising ``CLIENT_DEPRECATE_EOF`` makes the proxy responsible for
        OK-terminated result sets. StarRocks terminates with classic EOF
        packets, and the ``mysql`` 8.0.46 CLI drops the connection when it
        negotiates the flag and then sees an EOF. Not offering it keeps both
        sides in the mode the engine is actually in.
        """
        assert not p.SERVER_CAPABILITIES & p.CLIENT_DEPRECATE_EOF

    def test_advertises_the_capabilities_it_implements(self):
        for capability in (
            p.CLIENT_PROTOCOL_41,
            p.CLIENT_PLUGIN_AUTH,
            p.CLIENT_CONNECT_WITH_DB,
            p.CLIENT_SECURE_CONNECTION,
            p.CLIENT_CONNECT_ATTRS,
        ):
            assert p.SERVER_CAPABILITIES & capability, hex(capability)

    def test_rejects_a_short_scramble(self):
        with pytest.raises(ValueError):
            p.build_handshake_packet(1, b"tooshort")


class TestHandshakeResponseParsing:
    """Parse by capability bit, not by fixed offset.

    These two responses differ by four fields and only the capability flags
    explain the difference; an offset-based parser reads the auth response as
    the database name on one of them.
    """

    @staticmethod
    def _response(
        *,
        capabilities: int,
        username: bytes = b"nova_admin",
        auth_response: bytes = b"",
        database: bytes | None = None,
        plugin: bytes | None = None,
        attrs: bytes = b"",
    ) -> bytes:
        payload = struct.pack("<IIB", capabilities, 16 * 1024 * 1024, 45)
        payload += b"\x00" * 23
        payload += username + b"\x00"
        if capabilities & p.CLIENT_PLUGIN_AUTH_LENENC_CLIENT_DATA:
            payload += p.encode_length_encoded_str(auth_response)
        elif capabilities & p.CLIENT_SECURE_CONNECTION:
            payload += bytes([len(auth_response)]) + auth_response
        else:
            payload += auth_response + b"\x00"
        if database is not None:
            payload += database + b"\x00"
        if plugin is not None:
            payload += plugin + b"\x00"
        if capabilities & p.CLIENT_CONNECT_ATTRS:
            payload += p.encode_length_encoded_str(attrs)
        return payload

    @staticmethod
    def _attrs_blob(pairs: dict[str, str]) -> bytes:
        """Encode the CONNECT_ATTRS value itself (the handshake wraps it again)."""
        blob = b""
        for key, value in pairs.items():
            blob += p.encode_length_encoded_str(key.encode())
            blob += p.encode_length_encoded_str(value.encode())
        return blob

    def test_reads_username_and_auth_response(self):
        response = p.parse_handshake_response(
            self._response(
                capabilities=p.CLIENT_PROTOCOL_41
                | p.CLIENT_PLUGIN_AUTH_LENENC_CLIENT_DATA
                | p.CLIENT_PLUGIN_AUTH,
                auth_response=b"x" * 20,
                plugin=b"mysql_native_password",
            )
        )
        assert response.username == "nova_admin"
        assert response.auth_response == b"x" * 20
        assert response.auth_plugin == "mysql_native_password"
        assert response.database is None

    def test_reads_database_when_connect_with_db_is_set(self):
        response = p.parse_handshake_response(
            self._response(
                capabilities=p.CLIENT_PROTOCOL_41
                | p.CLIENT_CONNECT_WITH_DB
                | p.CLIENT_PLUGIN_AUTH_LENENC_CLIENT_DATA
                | p.CLIENT_PLUGIN_AUTH,
                auth_response=b"y" * 20,
                database=b"NOVA_DEMO",
                plugin=b"mysql_native_password",
            )
        )
        assert response.database == "NOVA_DEMO"
        assert response.auth_response == b"y" * 20

    def test_database_is_absent_without_the_capability(self):
        """A client that does not set CONNECT_WITH_DB sends no such field.

        Same payload shape as the previous test, minus the flag and the field:
        the parser must not mistake the plugin name for a database.
        """
        response = p.parse_handshake_response(
            self._response(
                capabilities=p.CLIENT_PROTOCOL_41
                | p.CLIENT_PLUGIN_AUTH_LENENC_CLIENT_DATA
                | p.CLIENT_PLUGIN_AUTH,
                auth_response=b"y" * 20,
                plugin=b"mysql_native_password",
            )
        )
        assert response.database is None

    def test_reads_secure_connection_length_prefixed_auth(self):
        response = p.parse_handshake_response(
            self._response(
                capabilities=p.CLIENT_PROTOCOL_41
                | p.CLIENT_SECURE_CONNECTION
                | p.CLIENT_PLUGIN_AUTH,
                auth_response=b"z" * 20,
                plugin=b"mysql_native_password",
            )
        )
        assert response.auth_response == b"z" * 20

    def test_reads_connect_attributes(self):
        attrs = self._attrs_blob({"_client_name": "pymysql", "program_name": "mysql"})
        response = p.parse_handshake_response(
            self._response(
                capabilities=p.CLIENT_PROTOCOL_41
                | p.CLIENT_PLUGIN_AUTH_LENENC_CLIENT_DATA
                | p.CLIENT_CONNECT_ATTRS,
                auth_response=b"q" * 20,
                attrs=attrs,
            )
        )
        assert response.connect_attrs == {
            "_client_name": "pymysql",
            "program_name": "mysql",
        }

    def test_without_protocol_41_is_refused(self):
        payload = struct.pack("<IIB", 0, 16 * 1024 * 1024, 45) + b"\x00" * 23
        with pytest.raises(p.ProtocolError):
            p.parse_handshake_response(payload)

    def test_truncated_payload_is_refused(self):
        with pytest.raises(p.ProtocolError):
            p.parse_handshake_response(b"short")


class TestOkErrorAndEof:
    def test_ok_packet_uses_length_encoded_affected_rows(self):
        payload = p.build_ok_packet(300, 0)
        reader = p.PacketReader(payload)
        assert reader.read_uint8() == 0x00
        assert reader.read_length_encoded_int() == 300
        assert reader.read_length_encoded_int() == 0  # last_insert_id

    def test_ok_packet_reports_zero_last_insert_id(self):
        """``QueryResult`` has no ``last_insert_id``; the proxy sends 0.

        Reporting a fabricated id would be worse than reporting none: a client
        that trusts it would fetch the wrong row.
        """
        payload = p.build_ok_packet(0, 0)
        reader = p.PacketReader(payload)
        reader.read_uint8()
        reader.read_length_encoded_int()
        assert reader.read_length_encoded_int() == 0

    def test_ok_packet_carries_status_and_warnings(self):
        payload = p.build_ok_packet(1, 0, status_flags=0x0002, warnings=3)
        assert struct.unpack("<HH", payload[-4:]) == (0x0002, 3)

    def test_error_packet_shape(self):
        payload = p.build_error_packet(1045, "Access denied for user 'x'")
        assert payload[0] == 0xFF
        assert struct.unpack("<H", payload[1:3])[0] == 1045
        assert payload[3:4] == b"#"
        assert payload[4:9] == b"HY000"
        assert payload[9:] == b"Access denied for user 'x'"

    def test_error_packet_flattens_newlines_and_tracebacks(self):
        """A traceback on the wire is unreadable and one line is enough.

        The engine's error text is multi-line; the client gets a single line so
        the message cannot be mistaken for protocol output.
        """
        payload = p.build_error_packet(1064, "line one\nline two\tindented")
        message = payload[9:].decode()
        assert "\n" not in message
        assert "\t" not in message
        assert message == "line one line two indented"

    def test_error_packet_uses_syntax_sqlstate_for_1064(self):
        payload = p.build_error_packet(1064, "bad sql", sqlstate=p.SQLSTATE_SYNTAX)
        assert payload[4:9] == b"42000"

    def test_eof_packet_shape(self):
        assert p.build_eof_packet(status_flags=0x0002)[:1] == b"\xfe"


class TestColumnDefinition:
    def test_encodes_every_length_encoded_field(self):
        payload = p.build_column_definition(p.ColumnDefinition(name="amount"))
        reader = p.PacketReader(payload)
        assert reader.read_length_encoded_bytes() == b"def"
        assert reader.read_length_encoded_bytes() == b""
        assert reader.read_length_encoded_bytes() == b""
        assert reader.read_length_encoded_bytes() == b""
        assert reader.read_length_encoded_bytes() == b"amount"
        assert reader.read_length_encoded_bytes() == b"amount"
        assert reader.read_uint8() == 0x0C
        assert reader.read_uint16() == p.CHARSET_UTF8
        assert reader.read_uint32() == 1024
        assert reader.read_uint8() == p.TYPE_VAR_STRING
        assert reader.read_uint16() == 0
        assert reader.read_uint8() == 0
        assert reader.read(2) == b"\x00\x00"

    def test_non_ascii_column_names_survive(self):
        payload = p.build_column_definition(p.ColumnDefinition(name="nama_penjual"))
        assert b"nama_penjual" in payload


class TestTextRow:
    def test_null_is_the_fb_marker(self):
        columns = [p.ColumnDefinition(name="a")]
        assert p.build_text_row([None], columns) == b"\xfb"

    def test_values_are_length_prefixed_utf8(self):
        columns = [p.ColumnDefinition(name="a"), p.ColumnDefinition(name="b")]
        payload = p.build_text_row(["hi", 42], columns)
        assert payload == b"\x02hi\x0242"

    def test_empty_string_is_not_null(self):
        columns = [p.ColumnDefinition(name="a")]
        assert p.build_text_row([""], columns) == b"\x00"
        assert p.build_text_row([None], columns) == b"\xfb"

    def test_bytes_values_pass_through(self):
        columns = [p.ColumnDefinition(name="a", type_code=p.TYPE_BLOB, charset=63)]
        payload = p.build_text_row([b"\x00\x01\xff"], columns)
        assert payload == b"\x03\x00\x01\xff"

    def test_long_value_uses_a_multi_byte_length(self):
        columns = [p.ColumnDefinition(name="a")]
        payload = p.build_text_row(["x" * 300], columns)
        assert payload[:3] == p.encode_length_encoded_int(300)

    def test_float_rendering_is_stable(self):
        columns = [p.ColumnDefinition(name="a")]
        payload = p.build_text_row([1.5], columns)
        assert payload == b"\x031.5"


class TestResultsetLayout:
    """The two terminator layouts, chosen by ``CLIENT_DEPRECATE_EOF``.

    A client desynchronises the moment the metadata/row blocks are terminated
    the wrong way — the symptom is "MySQL server has gone away" right after
    login, with no query error to explain it.
    """

    COLUMNS = [p.ColumnDefinition(name="a")]
    ROWS = [[1], [2]]

    def test_classic_layout_is_eof_delimited(self):
        payloads = p.build_resultset(self.COLUMNS, self.ROWS)
        assert payloads[0] == b"\x01"  # column count
        assert payloads[1][:1] != b"\xfe"  # column definition
        assert payloads[2] == p.build_eof_packet()  # metadata closed
        # count + 1 metadata + EOF + 2 rows + EOF
        assert len(payloads) == 1 + 1 + 1 + 2 + 1
        assert payloads[-1] == p.build_eof_packet()

    def test_deprecate_eof_layout_is_ok_delimited(self):
        payloads = p.build_resultset(
            self.COLUMNS, self.ROWS, capabilities=p.CLIENT_DEPRECATE_EOF
        )
        assert payloads[2][:1] != b"\xfe"  # no EOF after metadata
        assert payloads[-1][0] == 0x00  # rows closed by OK
        # count + 1 metadata + 2 rows + OK
        assert len(payloads) == 1 + 1 + 2 + 1

    def test_no_rows_still_produces_a_terminator(self):
        classic = p.build_resultset(self.COLUMNS, [])
        assert classic[-1] == p.build_eof_packet()
        modern = p.build_resultset(self.COLUMNS, [], capabilities=p.CLIENT_DEPRECATE_EOF)
        assert modern[-1][0] == 0x00

    def test_column_count_matches_metadata_packets(self):
        columns = [p.ColumnDefinition(name=f"c{i}") for i in range(4)]
        payloads = p.build_resultset(columns, [])
        assert payloads[0] == p.encode_length_encoded_int(4)
        # count + 4 metadata + metadata-closing EOF + row-block-closing EOF
        assert len(payloads) == 1 + 4 + 1 + 1
