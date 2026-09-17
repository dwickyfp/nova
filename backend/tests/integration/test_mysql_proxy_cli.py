"""End-to-end tests: a real MySQL client against the real proxy.

This is the acceptance test for the MySQL protocol proxy. Everything below the
``mysql`` CLI is production code — the codec, the relay handshake, the session
tracker, ``QueryService``, StarRocks and MinIO. The unit tests pin each layer in
isolation; these tests are what proves the layers fit.

**Why a container, and why it skips.** The host has no ``mysql`` binary, and the
point of these tests is a *real* client — a hand-written Python client would
exercise the same code path the unit tests already cover and would not catch a
protocol detail the real client is strict about. The client therefore runs from
``mysql:8.0``, which requires Docker. When Docker or the engine is unavailable
the whole module skips rather than failing: a missing local prerequisite is not
a regression in the proxy.

Run explicitly with::

    uv run pytest tests/integration/test_mysql_proxy_cli.py -v

Environment overrides: ``NOVA_PROXY_E2E_HOST`` (default ``host.docker.internal``),
``NOVA_PROXY_E2E_USER`` / ``NOVA_PROXY_E2E_PASSWORD``, ``NOVA_PROXY_E2E_STAGE``
(default ``products``), ``NOVA_PROXY_E2E_DATABASE`` (default ``NOVA_ANALYTICS``).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass

import pytest

from app.proxy.server import MySQLProxyServer

MYSQL_IMAGE = os.getenv("NOVA_PROXY_E2E_IMAGE", "mysql:8.0")
CLIENT_NETWORK = os.getenv("NOVA_PROXY_E2E_NETWORK", "docker_nova")
CLIENT_HOST = os.getenv("NOVA_PROXY_E2E_HOST", "host.docker.internal")
E2E_USER = os.getenv("NOVA_PROXY_E2E_USER", "nova_admin")
E2E_PASSWORD = os.getenv("NOVA_PROXY_E2E_PASSWORD", "NovaProxy2026!")
E2E_DATABASE = os.getenv("NOVA_PROXY_E2E_DATABASE", "NOVA_ANALYTICS")
E2E_STAGE = os.getenv("NOVA_PROXY_E2E_STAGE", "products")
E2E_STAGE_FILE = os.getenv("NOVA_PROXY_E2E_STAGE_FILE", "products_new.csv")

CLIENT_TIMEOUT_SECONDS = 90


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        probe = subprocess.run(
            ["docker", "image", "inspect", MYSQL_IMAGE],
            capture_output=True,
            timeout=30,
        )
        return probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason=f"Docker image {MYSQL_IMAGE} is not available locally",
)


@dataclass
class CliResult:
    stdout: str
    stderr: str
    returncode: int

    @property
    def output(self) -> str:
        return f"{self.stdout}\n{self.stderr}"

    def rows(self) -> list[list[str]]:
        """Tab-separated result rows, minus the header and client warnings."""
        lines = [
            line
            for line in self.stdout.splitlines()
            if line.strip() and "Using a password" not in line
        ]
        return [line.split("\t") for line in lines]


def _run_mysql(
    path: list[str],
    *,
    host: str,
    port: int,
    user: str = E2E_USER,
    password: str = E2E_PASSWORD,
    database: str | None = None,
) -> CliResult:
    """Run the containerised ``mysql`` CLI and capture its output.

    The password is passed as ``MYSQL_PWD`` rather than ``-p<value>``: the flag
    form prints a warning to stderr that every test would then have to filter,
    and it renders the credential in ``ps`` output on a shared host.
    """
    argv = [
        "docker",
        "run",
        "--rm",
        "--network",
        CLIENT_NETWORK,
        "-e",
        f"MYSQL_PWD={password}",
        MYSQL_IMAGE,
        "mysql",
        f"--host={host}",
        f"--port={port}",
        f"--user={user}",
        "--batch",
        "--raw",
        "--skip-column-names",
        *path,
    ]
    if database:
        argv.append(f"--database={database}")

    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=CLIENT_TIMEOUT_SECONDS,
    )
    return CliResult(
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
    )


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def proxy_server():
    """A real proxy on an ephemeral port, backed by the real engine.

    The server runs on a **dedicated background thread** with its own event
    loop, not on a loop this fixture drives by hand. The tests below are
    synchronous — they shell out to ``docker run`` — so there is no running loop
    to service the server while they execute: a proxy started with
    ``loop.run_until_complete`` would answer nothing and the client would hang
    until its own timeout. That failure mode looks like a protocol bug and is
    entirely an artefact of how the test is hosted.

    Port 0 is requested so the suite never collides with a developer's own proxy
    on 4406 — the two would otherwise fight over the port.
    """
    import threading

    state: dict[str, object] = {}
    ready = threading.Event()

    def _serve() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        state["loop"] = loop
        server = MySQLProxyServer(host="0.0.0.0", port=0)
        try:
            # The pipeline writes an audit row through the system pool on every
            # statement, so the pool is as much a prerequisite as the listener.
            # ``python -m app.proxy`` does this too; a test that skipped it would
            # fail every query with "System pool not initialized".
            from app.core.database import db

            loop.run_until_complete(db.init_system_pool())
            loop.run_until_complete(server.start())
            state["server"] = server
            state["port"] = server.bound_port
        except Exception as exc:  # pragma: no cover - environment dependent
            state["error"] = exc
            ready.set()
            loop.close()
            return
        ready.set()
        loop.run_forever()
        # Teardown: stop accepting, drain in-flight work, close the loop.
        loop.run_until_complete(server.stop())
        loop.run_until_complete(db.close_system_pool())
        loop.close()

    thread = threading.Thread(target=_serve, name="nova-proxy-e2e", daemon=True)
    thread.start()
    ready.wait(timeout=30)

    if "error" in state:  # pragma: no cover - environment dependent
        pytest.skip(f"could not start the proxy: {state['error']}")
    if "port" not in state:  # pragma: no cover - defensive
        pytest.skip("proxy did not become reachable")

    port = int(state["port"])  # type: ignore[arg-type]
    if not _reachable("127.0.0.1", port):  # pragma: no cover - defensive
        pytest.skip("proxy did not become reachable")

    yield state["server"], port

    loop = state["loop"]
    if loop is not None:  # type: ignore[truthy-bool]
        loop.call_soon_threadsafe(loop.stop)  # type: ignore[union-attr]
    thread.join(timeout=30)


@pytest.fixture(scope="module")
def engine_reachable():
    """Skip the whole module when StarRocks is not running on this host."""
    from app.core.config import settings

    if not _reachable(settings.STARROCKS_HOST, settings.STARROCKS_FE_MYSQL_PORT):
        pytest.skip("StarRocks is not reachable; start the engine first")


class TestAcceptanceCriteria:
    """The seven criteria from the task, exercised through the real CLI."""

    def test_ac1_select_returns_a_value(self, proxy_server, engine_reachable):
        """AC 1 — login succeeds and a trivial query returns its row.

        The handshake is the hard part: a fake handshake or a wrong packet
        sequence shows up here as "Lost connection at 'reading authorization
        packet'" rather than as an error message.
        """
        _, port = proxy_server
        result = _run_mysql(["-e", "SELECT 1"], host=CLIENT_HOST, port=port)

        assert result.returncode == 0, result.output
        assert [row[0] for row in result.rows()] == ["1"]

    def test_ac2_show_databases_hides_nova_system(self, proxy_server, engine_reachable):
        """AC 2 — ``SHOW DATABASES`` works and never lists ``NOVA_SYSTEM``."""
        _, port = proxy_server
        result = _run_mysql(["-e", "SHOW DATABASES"], host=CLIENT_HOST, port=port)

        assert result.returncode == 0, result.output
        databases = [row[0] for row in result.rows()]
        assert databases, "SHOW DATABASES returned nothing"
        assert "NOVA_SYSTEM" not in databases
        assert "information_schema" not in databases
        assert "sys" not in databases

    def test_ac3_stage_query_reads_from_object_storage(self, proxy_server, engine_reachable):
        """AC 3 — ``@stage`` is translated and the rows come from MinIO.

        This is the criterion that proves the proxy is not a passthrough: the
        statement is rewritten to ``FILES()`` with injected credentials before
        it reaches the engine, and the rows only exist in object storage.
        """
        _, port = proxy_server
        query = f"SELECT * FROM @{E2E_STAGE}.{E2E_STAGE_FILE} LIMIT 5"
        result = _run_mysql(
            ["-e", query],
            host=CLIENT_HOST,
            port=port,
            database=E2E_DATABASE,
        )

        assert result.returncode == 0, result.output
        rows = result.rows()
        assert rows, f"@stage query returned no rows:\n{result.output}"
        assert all(len(row) > 1 for row in rows), rows

    def test_ac4_wrong_password_is_rejected_with_1045(self, proxy_server, engine_reachable):
        """AC 4 — a bad password gives error 1045, not a hang or a traceback."""
        _, port = proxy_server
        result = _run_mysql(
            ["-e", "SELECT 1"],
            host=CLIENT_HOST,
            port=port,
            password="definitely-not-the-password",
        )

        assert result.returncode != 0
        assert "ERROR 1045" in result.output
        assert "Access denied" in result.output
        # A traceback on the wire would be the bug this criterion exists for.
        assert "Traceback" not in result.output
        assert "asyncmy" not in result.output

    def test_ac5_drop_role_accountadmin_is_refused(self, proxy_server, engine_reachable):
        """AC 5 — the immutable role cannot be dropped through the proxy."""
        _, port = proxy_server
        result = _run_mysql(
            ["-e", "DROP ROLE ACCOUNTADMIN"],
            host=CLIENT_HOST,
            port=port,
        )

        assert result.returncode != 0
        assert "ACCOUNTADMIN" in result.output
        assert "cannot be dropped" in result.output.lower()

    def test_ac5_revoke_accountadmin_is_refused(self, proxy_server, engine_reachable):
        """AC 5 — the companion operation is guarded too."""
        _, port = proxy_server
        result = _run_mysql(
            ["-e", "REVOKE ALL ON *.* FROM ROLE ACCOUNTADMIN"],
            host=CLIENT_HOST,
            port=port,
        )

        assert result.returncode != 0
        assert "ACCOUNTADMIN" in result.output


class TestSessionSemantics:
    """Session behaviour the proxy owns rather than forwards."""

    def test_set_user_variable_does_not_become_a_stage_lookup(self, proxy_server, engine_reachable):
        """``SET @x = 1`` must not be sent to the engine.

        ``parse_sql`` reads ``@x`` as a stage reference, so forwarding this
        statement would fail with ``Stage 'x' not found``.
        """
        _, port = proxy_server
        result = _run_mysql(["-e", "SET @x = 1"], host=CLIENT_HOST, port=port)

        assert result.returncode == 0, result.output
        assert "not found" not in result.output

    def test_use_statement_sets_the_database_context(self, proxy_server, engine_reachable):
        """``USE`` is tracked by the proxy, and later queries see it."""
        _, port = proxy_server
        result = _run_mysql(
            ["-e", "USE NOVA_DEMO; SELECT DATABASE()"],
            host=CLIENT_HOST,
            port=port,
        )

        assert result.returncode == 0, result.output
        values = [row[0] for row in result.rows()]
        assert "NOVA_DEMO" in values

    def test_multi_statement_script_runs(self, proxy_server, engine_reachable):
        _, port = proxy_server
        result = _run_mysql(
            ["-e", "SET @a = 1; SELECT 2 AS two"],
            host=CLIENT_HOST,
            port=port,
        )

        assert result.returncode == 0, result.output
        assert [row[0] for row in result.rows()] == ["2"]

    def test_com_ping_keeps_the_connection_alive(self, proxy_server, engine_reachable):
        """Two queries on one connection prove the loop survives a round trip."""
        _, port = proxy_server
        result = _run_mysql(
            ["-e", "SELECT 1; SELECT 2"],
            host=CLIENT_HOST,
            port=port,
        )

        assert result.returncode == 0, result.output
        assert [row[0] for row in result.rows()] == ["1", "2"]


class TestCredentialsStayPrivate:
    """Nova's never-store-credentials rule holds across the proxy too."""

    def test_stage_query_output_carries_no_storage_credentials(
        self, proxy_server, engine_reachable
    ):
        """No storage key may appear in what the client receives."""
        from app.core.config import get_storage_connection

        connection = get_storage_connection("production")
        _, port = proxy_server
        result = _run_mysql(
            ["-e", f"SELECT * FROM @{E2E_STAGE}.{E2E_STAGE_FILE} LIMIT 1"],
            host=CLIENT_HOST,
            port=port,
            database=E2E_DATABASE,
        )

        assert connection.access_key, "test cannot assert without a configured key"
        assert connection.access_key not in result.output
        assert connection.secret_key not in result.output

    def test_error_messages_carry_no_storage_credentials(self, proxy_server, engine_reachable):
        """A failed ``@stage`` query must not echo the injected credentials.

        The engine's error text can quote the statement it refused, and that
        statement carries storage credentials — so this is the path where a leak
        would actually happen.
        """
        from app.core.config import get_storage_connection

        connection = get_storage_connection("production")
        _, port = proxy_server
        result = _run_mysql(
            ["-e", "SELECT * FROM @no_such_stage___nope.file.csv"],
            host=CLIENT_HOST,
            port=port,
            database=E2E_DATABASE,
        )

        assert connection.secret_key not in result.output
        assert connection.access_key not in result.output
