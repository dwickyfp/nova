"""Run the real local Ranger security acceptance scenario.

Execute this inside the running ``nova-backend`` container. The script speaks
enough MySQL protocol to attach Nova's ``nova_role`` connection attribute,
then exercises the same assistant SQL tool used by Nova Studio.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

CLIENT_CONNECT_WITH_DB = 1 << 3
CLIENT_PROTOCOL_41 = 1 << 9
CLIENT_SECURE_CONNECTION = 1 << 15
CLIENT_PLUGIN_AUTH = 1 << 19
CLIENT_CONNECT_ATTRS = 1 << 20


class MySQLError(RuntimeError):
    pass


def _lenenc(value: int) -> bytes:
    if value < 0xFB:
        return bytes([value])
    if value <= 0xFFFF:
        return b"\xfc" + struct.pack("<H", value)
    if value <= 0xFFFFFF:
        return b"\xfd" + value.to_bytes(3, "little")
    return b"\xfe" + struct.pack("<Q", value)


def _read_lenenc(data: bytes, offset: int) -> tuple[int | None, int]:
    marker = data[offset]
    if marker < 0xFB:
        return marker, offset + 1
    if marker == 0xFB:
        return None, offset + 1
    sizes = {0xFC: 2, 0xFD: 3, 0xFE: 8}
    size = sizes[marker]
    start = offset + 1
    return int.from_bytes(data[start : start + size], "little"), start + size


def _native_password(password: str, scramble: bytes) -> bytes:
    if not password:
        return b""
    stage1 = hashlib.sha1(password.encode()).digest()  # noqa: S324 - MySQL protocol
    stage2 = hashlib.sha1(stage1).digest()  # noqa: S324 - MySQL protocol
    challenge = hashlib.sha1(scramble + stage2).digest()  # noqa: S324 - MySQL protocol
    return bytes(left ^ right for left, right in zip(stage1, challenge, strict=True))


@dataclass
class MySQLClient:
    username: str
    password: str
    database: str | None = "analytics"
    role: str | None = None
    host: str = "127.0.0.1"
    port: int = 4406

    def __post_init__(self) -> None:
        self._socket: socket.socket | None = None

    def __enter__(self) -> MySQLClient:
        self._socket = socket.create_connection((self.host, self.port), timeout=15)
        handshake = self._read_packet()
        self._authenticate(handshake)
        return self

    def __exit__(self, *_args: object) -> None:
        if self._socket is not None:
            try:
                self._send_packet(b"\x01", 0)
            finally:
                self._socket.close()

    def _recv_exact(self, size: int) -> bytes:
        assert self._socket is not None
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = self._socket.recv(remaining)
            if not chunk:
                raise MySQLError("MySQL connection closed unexpectedly")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_packet(self) -> bytes:
        header = self._recv_exact(4)
        length = int.from_bytes(header[:3], "little")
        return self._recv_exact(length)

    def _send_packet(self, payload: bytes, sequence: int) -> None:
        assert self._socket is not None
        header = len(payload).to_bytes(3, "little") + bytes([sequence])
        self._socket.sendall(header + payload)

    @staticmethod
    def _error(payload: bytes) -> MySQLError:
        code = int.from_bytes(payload[1:3], "little")
        message = payload[9:].decode(errors="replace") if payload[3:4] == b"#" else ""
        return MySQLError(f"MySQL error {code}: {message}")

    def _authenticate(self, handshake: bytes) -> None:
        offset = 1
        offset = handshake.index(b"\0", offset) + 1
        offset += 4
        scramble1 = handshake[offset : offset + 8]
        offset += 9
        server_caps = int.from_bytes(handshake[offset : offset + 2], "little")
        offset += 2
        offset += 1 + 2
        server_caps |= int.from_bytes(handshake[offset : offset + 2], "little") << 16
        offset += 2
        auth_length = handshake[offset]
        offset += 11
        scramble2_size = max(13, auth_length - 8)
        scramble2 = handshake[offset : offset + scramble2_size].rstrip(b"\0")
        offset += scramble2_size
        plugin = handshake[offset:].split(b"\0", 1)[0] or b"mysql_native_password"
        if plugin != b"mysql_native_password":
            raise MySQLError(f"Unsupported authentication plugin {plugin.decode()}")

        capabilities = (
            CLIENT_PROTOCOL_41
            | CLIENT_SECURE_CONNECTION
            | CLIENT_PLUGIN_AUTH
            | CLIENT_CONNECT_ATTRS
        ) & server_caps
        if self.database:
            capabilities |= CLIENT_CONNECT_WITH_DB & server_caps
        auth = _native_password(self.password, (scramble1 + scramble2)[:20])
        attrs = {
            "_client_name": "nova-ranger-acceptance",
            "_client_version": "1",
        }
        if self.role:
            attrs["nova_role"] = self.role
        encoded_attrs = b"".join(
            _lenenc(len(key.encode()))
            + key.encode()
            + _lenenc(len(value.encode()))
            + value.encode()
            for key, value in attrs.items()
        )
        response_parts = [
            struct.pack("<I", capabilities),
            struct.pack("<I", 16 * 1024 * 1024),
            b"\x21",
            b"\0" * 23,
            self.username.encode() + b"\0",
            bytes([len(auth)]) + auth,
        ]
        if self.database:
            response_parts.append(self.database.encode() + b"\0")
        response_parts.extend(
            [plugin + b"\0", _lenenc(len(encoded_attrs)) + encoded_attrs]
        )
        response = b"".join(response_parts)
        self._send_packet(response, 1)
        verdict = self._read_packet()
        if verdict[:1] == b"\xff":
            raise self._error(verdict)
        if verdict[:1] != b"\0":
            raise MySQLError("Unexpected MySQL authentication response")

    def query(self, sql: str) -> list[list[str | None]]:
        self._send_packet(b"\x03" + sql.encode(), 0)
        first = self._read_packet()
        if first[:1] == b"\xff":
            raise self._error(first)
        if first[:1] == b"\0":
            return []
        column_count, _ = _read_lenenc(first, 0)
        assert column_count is not None
        for _ in range(column_count):
            self._read_packet()
        self._read_packet()

        rows: list[list[str | None]] = []
        while True:
            payload = self._read_packet()
            if payload[:1] == b"\xfe" and len(payload) < 9:
                return rows
            offset = 0
            row: list[str | None] = []
            for _ in range(column_count):
                length, offset = _read_lenenc(payload, offset)
                if length is None:
                    row.append(None)
                    continue
                row.append(payload[offset : offset + length].decode(errors="replace"))
                offset += length
            rows.append(row)


def _assert_cities(rows: list[list[str | None]], expected: set[str]) -> None:
    cities = {str(row[1]) for row in rows}
    if cities != expected:
        raise AssertionError(f"Expected cities {sorted(expected)}, got {sorted(cities)}")


def verify_proxy() -> None:
    with MySQLClient("alice", "NovaAlice2026!", role="marketing") as alice:
        assert alice.query("SELECT CURRENT_ROLE()") == [["marketing"]]
        _assert_cities(alice.query("SELECT id, city, amount FROM analytics.sales"), {"Jakarta"})
        assert alice.query(
            "SELECT id, city, amount FROM analytics.sales WHERE city='Bandung'"
        ) == []
        alice.query("USE ROLE regional_manager")
        assert alice.query("SELECT CURRENT_ROLE()") == [["regional_manager"]]
        _assert_cities(
            alice.query("SELECT id, city, amount FROM analytics.sales"),
            {"Jakarta", "Bandung"},
        )
        print("Proxy: alice / regional_manager -> Bandung, Jakarta")
    print(
        "Proxy: alice / marketing -> Jakarta; explicit Bandung predicate -> empty"
    )

    with MySQLClient("bob", "NovaBob2026!", role="marketing") as bob:
        assert bob.query("SELECT CURRENT_ROLE()") == [["marketing"]]
        _assert_cities(bob.query("SELECT id, city, amount FROM analytics.sales"), {"Bandung"})
    print("Proxy: bob / marketing -> Bandung")

    with MySQLClient(
        "alice", "NovaAlice2026!", database=None, role="finance"
    ) as finance:
        assert finance.query("SELECT CURRENT_ROLE()") == [["finance"]]
        try:
            finance.query("SELECT id, city, amount FROM analytics.sales")
        except MySQLError:
            pass
        else:
            raise AssertionError("Assigned but inactive roles leaked into Ranger authorization")
    print("Proxy: alice / finance -> analytics.sales denied")

    with MySQLClient(
        "alice", "NovaAlice2026!", role="marketing"
    ) as marketing_connection, MySQLClient(
        "alice", "NovaAlice2026!", database=None, role="finance"
    ) as finance_connection:
        assert marketing_connection.query("SELECT CURRENT_ROLE()") == [["marketing"]]
        assert finance_connection.query("SELECT CURRENT_ROLE()") == [["finance"]]
        _assert_cities(
            marketing_connection.query("SELECT id, city, amount FROM analytics.sales"),
            {"Jakarta"},
        )
        try:
            finance_connection.query("SELECT id, city, amount FROM analytics.sales")
        except MySQLError:
            pass
        else:
            raise AssertionError("Simultaneous sessions contaminated each other's roles")
    print("Connections: alice / marketing and alice / finance remained isolated")

    raw_phone_values = {"+62-811-0001", "+62-811-0002"}
    with MySQLClient("alice", "NovaAlice2026!", role="marketing") as masked:
        phones = masked.query("SELECT phone FROM analytics.customers ORDER BY id")
        if any(value and value[0] in raw_phone_values for value in phones):
            raise AssertionError("Marketing received an unmasked phone value")
        masked.query("USE ROLE regional_manager")
        assert masked.query("SELECT CURRENT_ROLE()") == [["regional_manager"]]
        raw_phones = masked.query("SELECT phone FROM analytics.customers ORDER BY id")
        expected_raw = [
            ["+62-811-0001"],
            ["+62-811-0002"],
        ]
        if raw_phones != expected_raw:
            raise AssertionError(
                f"Regional manager did not receive raw phone values: {raw_phones!r}"
            )
        masked.query("USE ROLE marketing")
        assert masked.query("SELECT CURRENT_ROLE()") == [["marketing"]]
        phones = masked.query("SELECT phone FROM analytics.customers ORDER BY id")
        if any(value and value[0] in raw_phone_values for value in phones):
            raise AssertionError("Mask did not return after switching back to marketing")
    print("Mask: marketing masked -> regional_manager raw -> marketing masked")

    try:
        with MySQLClient("alice", "NovaAlice2026!", role="SECURITYADMIN"):
            pass
    except MySQLError:
        pass
    else:
        raise AssertionError("An unassigned connection-time role was accepted")

    try:
        with MySQLClient("root", ""):
            pass
    except MySQLError:
        pass
    else:
        raise AssertionError("root was accepted by the public Nova proxy")


def _inherit_pid_one_environment() -> None:
    if os.name != "posix" or not os.path.exists("/proc/1/environ"):
        return
    with open("/proc/1/environ", "rb") as environ_file:  # noqa: PTH123
        raw = environ_file.read()
    for item in raw.split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        os.environ.setdefault(key.decode(), value.decode())


async def verify_agent_tool() -> None:
    _inherit_pid_one_environment()
    from app.core.database import db
    from app.core.security import encrypt_password
    from app.modules.assistant.service import LoopContext
    from app.modules.assistant.tools import ToolInvocation
    from app.modules.assistant.tools.query_execute import QueryExecuteTool

    async def run(username: str, password: str, expected: str, forbidden: str) -> None:
        context = LoopContext(
            user_name=username,
            database="analytics",
            schema_name=None,
            role="marketing",
            workspace_file_id=None,
            session_id=f"ranger-e2e-{username}",
            thread_id=f"ranger-e2e-{username}",
            user={
                "username": username,
                "encrypted_password": encrypt_password(password),
                "active_role": "marketing",
                "security_context_version": 1,
                "session_id": f"ranger-e2e-{username}",
            },
        )
        invocation = ToolInvocation(
            tool_call_id=f"ranger-e2e-{username}",
            tool_name="query_execute",
            arguments={"sql": "SELECT id, city, amount FROM analytics.sales"},
        )
        outcome = await QueryExecuteTool().run(invocation, context)
        if not outcome.ok or expected not in outcome.summary or forbidden in outcome.summary:
            raise AssertionError(f"Agent scope check failed for {username}: {outcome}")
        print(f"Agent: {username} / marketing -> {expected} only")

    await db.init_system_pool()
    try:
        await run("alice", "NovaAlice2026!", "Jakarta", "Bandung")
        await run("bob", "NovaBob2026!", "Bandung", "Jakarta")
    finally:
        await db.close_system_pool()


async def main() -> None:
    verify_proxy()
    await verify_agent_tool()
    print("Ranger E2E passed: proxy roles, RLS, mask, root guard, and Agent tool")


if __name__ == "__main__":
    asyncio.run(main())
