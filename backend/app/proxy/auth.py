"""Authentication for the MySQL proxy.

Nova has exactly one source of truth for credentials — StarRocks, reached
through ``AuthService.verify_credentials``. This module does not add a second,
and it never learns a password either.

**How the handshake is authenticated.** ``docs/arch-07-mysql-proxy.md`` sketches
a hand-written handshake with a hardcoded byte layout and no real verification.
The delegation note proposed two alternatives. The one used here is the second
half of its first option, and the reason is that no other option is sound:

* *Send Nova's own ``mysql_native_password`` handshake, with the password sent
  cleartext alongside it.* Rejected: MySQL clients do **not** send a cleartext
  password. Measured against the real ``mysql`` 8.0.46 client, the handshake
  response carries a 20-byte scramble hash in every capability combination —
  the client sets ``CLIENT_PLUGIN_AUTH`` regardless of what the server
  advertises, and the auth-response field is the hash. There is no capability
  bit that makes a modern client send its password in the clear. A proxy built
  on that premise cannot authenticate a real client at all.
* *Recover the password from the response.* Rejected: wrong per the same
  measurement, and it is a one-way function by design.
* *Relay StarRocks' own challenge.* Used. StarRocks speaks
  ``mysql_native_password`` (verified: its handshake advertises that plugin and
  a 20-byte scramble). The proxy therefore takes StarRocks' scramble, presents
  it to the client as its own, and forwards the client's response to StarRocks
  verbatim. StarRocks does the verification against the hash it holds; the proxy
  never sees, derives or stores a password.

**What the relay buys and costs.** Nothing secret crosses this module — the
scramble and the response are a one-time challenge/response pair, useless to a
replay because the next connection gets a fresh scramble. The cost is that the
proxy's login path is coupled to StarRocks' auth plugin: if StarRocks is ever
configured for ``caching_sha2_password``, the relay stops completing and this
module must change. That coupling is deliberate — it is the *same* coupling a
client would have connecting to StarRocks directly, so the proxy cannot drift
from the engine's own auth rules. ``read_starrocks_challenge`` fails closed if
the plugin is not ``mysql_native_password``.

**What the relay does not do.** It does not verify the response itself, because
it cannot: only StarRocks holds the hash. Verification *is* the relay — StarRocks
returns OK or ``ER_ACCESS_DENIED_ERROR``, and the proxy maps that to its own
handshake outcome. This is why an invalid login is refused by the party that
owns the rule (lockouts, expired passwords, disabled users included) rather than
by a comparison that could only ever know what StarRocks already decided.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
from dataclasses import dataclass, field

import asyncmy

from app.core.config import settings

logger = logging.getLogger(__name__)

NATIVE_PLUGIN_NAME = b"mysql_native_password"

#: SERVER_STATUS_AUTOCOMMIT. A StarRocks session is autocommit, and the adopted
#: connection must report the same status or ``Cursor`` sends spurious COMMITs.
SERVER_STATUS_AUTOCOMMIT = 1 << 1

#: StarRocks' own ER_ACCESS_DENIED_ERROR.
ER_ACCESS_DENIED_ERROR = 1045


class AuthenticationError(Exception):
    """The client could not be authenticated. Carries a client-safe message."""

    def __init__(self, message: str, *, code: int = 1045) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class StarRocksChallenge:
    """The scramble and plugin StarRocks offered on a live connection."""

    scramble: bytes
    plugin: bytes
    capabilities: int


@dataclass
class StarRocksLogin:
    """A live, half-completed login to StarRocks.

    Holds the connection open between the two halves of the relay, because a
    ``mysql_native_password`` response is valid only against the scramble of
    *its own* connection: StarRocks generates a fresh scramble per connection,
    so reading a challenge on one socket and sending the response on another
    fails with ``ER_ACCESS_DENIED_ERROR`` even for a correct password. The two
    steps therefore have to share this object.
    """

    scramble: bytes
    plugin: bytes
    capabilities: int
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    _session: asyncmy.Connection | None = field(default=None, repr=False)

    async def finish(
        self, *, username: str, auth_response: bytes, database: str | None = None
    ) -> None:
        """Send the client's response on this connection and read the verdict.

        ``database`` is accepted for symmetry with the client handshake but is
        **not** forwarded upstream: StarRocks answers
        ``CLIENT_CONNECT_WITH_DB`` in the handshake with error 5501, so the
        upstream login is always sent without it. The database the client named
        is tracked in the proxy's own session instead and passed to
        ``QueryService`` per statement, which is also the channel the HTTP API
        uses.

        Raises:
            AuthenticationError: StarRocks refused the login.
        """
        del database
        payload = _build_handshake_response(username, auth_response, None)
        self.writer.write(length_header(payload) + b"\x01" + payload)
        await self.writer.drain()

        header = await self.reader.readexactly(4)
        length = int.from_bytes(header[:3], "little")
        reply = await self.reader.readexactly(length)

        if reply and reply[0] == 0xFF:
            code = (
                struct.unpack("<H", reply[1:3])[0]
                if len(reply) >= 3
                else ER_ACCESS_DENIED_ERROR
            )
            if code == ER_ACCESS_DENIED_ERROR:
                raise AuthenticationError(f"Access denied for user '{username}'")
            raise AuthenticationError(f"StarRocks refused the login (error {code})")

        if not reply or reply[0] != 0x00:
            raise AuthenticationError(f"Access denied for user '{username}'")

    async def open_session(self) -> asyncmy.Connection:
        """Wrap this authenticated socket in an ``asyncmy`` connection.

        The relay leaves a socket that is already authenticated as the client's
        user, and ``asyncmy.Cursor`` is what parses StarRocks' responses into
        rows. Rather than re-implementing that parsing, the socket is adopted by
        an ``asyncmy.Connection`` whose normal connect step is skipped — every
        attribute ``Cursor`` touches is set to the state a successful
        ``connect()`` would have left behind, and ``autocommit`` matches the
        server's since a StarRocks session is autocommit by default.

        This is the only way the proxy can run the user's statements under the
        user's own RBAC: it holds no password to hand
        ``StarRocksConnectionFactory.user_conn``, so it hands the authenticated
        connection instead (``QueryRepository.execute_as_user(connected=...)``).
        """
        conn = asyncmy.Connection(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user="",
            password="",
            autocommit=True,
        )
        conn._reader = self.reader
        conn._writer = self.writer
        conn._connected = True
        conn._next_seq_id = 0
        conn.server_status = SERVER_STATUS_AUTOCOMMIT
        conn.autocommit_mode = True
        self._session = conn
        return conn

    async def close(self) -> None:
        # If a query session was opened, closing it closes the socket too.
        if self._session is not None:
            with contextlib.suppress(Exception):
                self._session.close()
            return
        self.writer.close()
        with contextlib.suppress(Exception):
            await self.writer.wait_closed()

    async def __aenter__(self) -> StarRocksLogin:
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()


@dataclass(frozen=True)
class AuthenticatedUser:
    """A client StarRocks accepted."""

    username: str
    database: str | None = None
    roles: list[str] | None = None


async def open_starrocks_login(
    *,
    host: str | None = None,
    port: int | None = None,
    timeout: float = 10.0,
) -> StarRocksLogin:
    """Open a StarRocks login and read its challenge, leaving it open.

    The connection stays open (see :class:`StarRocksLogin`) so the client's
    response can be relayed back on the same socket. A fresh login per client
    connection means a fresh scramble, which is what makes the relayed response
    single-use.

    Raises:
        AuthenticationError: StarRocks is unreachable, or offers a plugin the
            proxy cannot relay.
    """
    target_host = host or settings.STARROCKS_HOST
    target_port = port or settings.STARROCKS_FE_MYSQL_PORT

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(target_host, target_port), timeout=timeout
        )
    except (OSError, TimeoutError) as exc:
        raise AuthenticationError(
            "Cannot reach the StarRocks authentication service"
        ) from exc

    try:
        header = await asyncio.wait_for(reader.readexactly(4), timeout=timeout)
        length = int.from_bytes(header[:3], "little")
        if length <= 0 or length > 1 << 20:
            raise AuthenticationError("Malformed handshake from StarRocks")
        body = await asyncio.wait_for(reader.readexactly(length), timeout=timeout)
    except AuthenticationError:
        writer.close()
        raise
    except (TimeoutError, asyncio.IncompleteReadError, OSError) as exc:
        writer.close()
        raise AuthenticationError("Malformed handshake from StarRocks") from exc

    try:
        challenge = parse_starrocks_handshake(body)
    except AuthenticationError:
        writer.close()
        raise

    return StarRocksLogin(
        scramble=challenge.scramble,
        plugin=challenge.plugin,
        capabilities=challenge.capabilities,
        reader=reader,
        writer=writer,
    )


def parse_starrocks_handshake(body: bytes) -> StarRocksChallenge:
    """Extract the scramble and plugin from a ``HandshakeV10`` payload.

    Parsed field by field rather than by fixed offsets: the layout after the
    capability flags depends on the advertised capability bits (the auth-plugin
    name is present only under ``CLIENT_PLUGIN_AUTH``), and the scramble is
    split across two fields with a filler between them. Reading it at a
    hardcoded offset is what makes a proxy break silently on an engine upgrade.
    """
    if not body or body[0] != 0x0A:
        raise AuthenticationError("Unsupported StarRocks handshake protocol version")

    index = 1
    end = body.find(b"\x00", index)
    if end < 0:
        raise AuthenticationError("Malformed StarRocks handshake")
    index = end + 1  # server version

    index += 4  # connection id
    first_half = body[index : index + 8]
    index += 8
    index += 1  # filler

    if index + 6 > len(body):
        raise AuthenticationError("Malformed StarRocks handshake")
    capability_low = struct.unpack("<H", body[index : index + 2])[0]
    index += 2
    index += 1  # charset
    index += 2  # status flags
    capability_high = struct.unpack("<H", body[index : index + 2])[0]
    index += 2
    capabilities = capability_low | (capability_high << 16)

    auth_plugin_data_length = 0
    if index < len(body):
        auth_plugin_data_length = body[index]
        index += 1
    index += 10  # reserved

    # The second half completes the 20-byte scramble. Taking ``20 - 8`` bytes
    # keeps the total visibly 20; the length byte (21 = 20 + NUL) only decides
    # whether the trailing NUL byte is consumed.
    remaining = max(20 - len(first_half), 0)
    if index + remaining > len(body):
        raise AuthenticationError("Malformed StarRocks handshake")
    second_half = body[index : index + remaining]
    index += remaining
    if auth_plugin_data_length == 21 and index < len(body) and body[index] == 0x00:
        index += 1

    plugin = b""
    if capabilities & 0x00080000 and index < len(body):  # CLIENT_PLUGIN_AUTH
        plugin_end = body.find(b"\x00", index)
        plugin = body[index:] if plugin_end < 0 else body[index:plugin_end]

    scramble = (first_half + second_half)[:20]
    if len(scramble) != 20:
        raise AuthenticationError("Malformed StarRocks handshake: short scramble")

    if plugin and plugin != NATIVE_PLUGIN_NAME:
        # Fail closed rather than relay a challenge under the wrong plugin.
        raise AuthenticationError(
            "StarRocks offers authentication plugin "
            f"{plugin.decode('ascii', errors='replace')!r}, which the Nova MySQL proxy "
            "cannot relay. Configure StarRocks with mysql_native_password."
        )

    return StarRocksChallenge(
        scramble=scramble,
        plugin=plugin or NATIVE_PLUGIN_NAME,
        capabilities=capabilities,
    )


def length_header(payload: bytes) -> bytes:
    return len(payload).to_bytes(3, "little")


#: Capability flags mirrored from ``app.proxy.protocol`` for the upstream
#: connection. Duplicated as a literal rather than imported so this module has
#: no cycle back into ``protocol``; the values are fixed by MySQL's protocol
#: and are asserted against ``protocol`` in the unit tests.
#:
#: Chosen to match what the upstream engine accepts, which was measured: a
#: response carrying ``CLIENT_CONNECT_WITH_DB`` is refused with error 5501, and
#: the lenenc form of the auth response is the one StarRocks parses.
_UPSTREAM_CAPABILITIES = (
    1  # CLIENT_LONG_PASSWORD
    | 4  # CLIENT_LONG_FLAG
    | 512  # CLIENT_PROTOCOL_41
    | 8192  # CLIENT_TRANSACTIONS
    | 131072  # CLIENT_MULTI_RESULTS
    | 524288  # CLIENT_PLUGIN_AUTH
    | 2097152  # CLIENT_PLUGIN_AUTH_LENENC_CLIENT_DATA
)


def _build_handshake_response(
    username: str, auth_response: bytes, database: str | None = None
) -> bytes:
    """Build the ``HandshakeResponse41`` the proxy sends upstream.

    Nova always omits the database: StarRocks refuses ``CLIENT_CONNECT_WITH_DB``
    in the handshake with error 5501 (measured against 4.1.1), so neither the
    flag nor the field is sent and the database context is carried by the
    proxy's session instead. The parameter is kept because the field layout is
    the point of this function and removing it would hide that.
    """
    del database
    capabilities = _UPSTREAM_CAPABILITIES

    payload = bytearray()
    payload += struct.pack("<I", capabilities)
    payload += struct.pack("<I", 16 * 1024 * 1024)
    payload += struct.pack("<B", 45)  # utf8mb4_general_ci
    payload += b"\x00" * 23
    payload += username.encode("utf-8") + b"\x00"
    payload += bytes([len(auth_response)]) + auth_response
    payload += NATIVE_PLUGIN_NAME + b"\x00"
    return bytes(payload)


__all__ = [
    "NATIVE_PLUGIN_NAME",
    "AuthenticatedUser",
    "AuthenticationError",
    "StarRocksChallenge",
    "StarRocksLogin",
    "open_starrocks_login",
    "parse_starrocks_handshake",
]
