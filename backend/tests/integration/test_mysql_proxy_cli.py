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


#: `engine` marks this suite as requiring the real stack from
#: docker-compose.test.yml, so the L3 CI job can select it with `-m engine`
#: and a markerless unit run never picks it up. The `skipif` stays: Docker and
#: a local mysql:8.0 client are genuinely optional for a developer, and a
#: missing prerequisite should skip, not fail. The CI job fails on
#: `skipped == collected` precisely so an all-skip run cannot pass for green.
pytestmark = [
    pytest.mark.engine,
    pytest.mark.skipif(
        not _docker_available(),
        reason=f"Docker image {MYSQL_IMAGE} is not available locally",
    ),
]


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


def _run_async(coro):
    """Run ``coro`` on a fresh event loop and close it.

    These tests mix a sync subprocess client with asyncmy reads. The implicit
    loop ``asyncio.get_event_loop()`` returns is shared, module-scoped state:
    once an async pytest module has run first, pytest-asyncio has torn that
    loop down and the implicit getter raises ``RuntimeError: There is no
    current event loop``. Owning the loop here removes the collection-order
    dependency rather than depending on it.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _latest_rewritten_sql(user_name: str) -> list[str]:
    """The most recent rewritten statements recorded for ``user_name``.

    Read straight from ``NOVA_SYSTEM.AUDIT_LOG`` over the host-reachable engine
    port. Only the **redacted** column is selected: the credential-bearing
    statement is never in this table by design (AGENTS.md credential rule), so
    this cannot leak a secret even if the redaction regressed.
    """
    import asyncmy

    from app.core.config import settings

    conn = await asyncmy.connect(
        host=settings.STARROCKS_HOST,
        port=settings.STARROCKS_FE_MYSQL_PORT,
        user=settings.STARROCKS_ROOT_USER,
        password=settings.STARROCKS_ROOT_PASSWORD,
    )
    try:
        async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
            await cur.execute(
                """
                SELECT rewritten_sql
                FROM NOVA_SYSTEM.AUDIT_LOG
                WHERE user_name = %s AND rewritten_sql IS NOT NULL
                ORDER BY event_time DESC LIMIT 10
                """,
                (user_name,),
            )
            return [row["rewritten_sql"] for row in await cur.fetchall()]
    finally:
        conn.close()


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

    def test_root_is_internal_only(self, proxy_server, engine_reachable):
        """The passwordless engine root must never be published on port 4406."""
        _, port = proxy_server
        result = _run_mysql(
            ["-e", "SELECT 1"],
            host=CLIENT_HOST,
            port=port,
            user="root",
            password="",
        )

        assert result.returncode != 0, result.output
        assert "Access denied for user 'root'" in result.output

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

    def test_ac3_engine_received_the_rewritten_files_statement(
        self, proxy_server, engine_reachable
    ):
        """AC 3 (stronger) — what reached the engine was the *rewrite*.

        ``test_ac3`` proves the rows are right. This proves why: the statement
        the engine executed was ``FILES()`` with the CSV properties applied,
        not the user's ``@stage`` text and not a translation that dropped the
        tuning.

        The audit log is the evidence surface. ``QueryService`` records the
        **redacted** rewritten statement in ``NOVA_SYSTEM.AUDIT_LOG``, so this
        reads what was sent without ever handling a credential value — the
        engine form carries real ones, the stored form does not.

        This is the L1 bug's L3 counterpart: ``_inject_files_params`` once
        suppressed the CSV pass because the translator had already written
        credentials into ``FILES()``, and the query then returned the file's
        raw line text as one column. That failure is invisible in the row count
        but obvious here.
        """
        _, port = proxy_server
        # Run it first so there is a rewritten statement to inspect, even when
        # this test is selected on its own.
        warm = _run_mysql(
            ["-e", f"SELECT * FROM @{E2E_STAGE}.{E2E_STAGE_FILE} LIMIT 1"],
            host=CLIENT_HOST,
            port=port,
            database=E2E_DATABASE,
        )
        assert warm.returncode == 0, warm.output

        rewritten = _run_async(_latest_rewritten_sql(E2E_USER))
        stage_statements = [sql for sql in rewritten if "FILES(" in sql]
        assert stage_statements, f"no FILES() rewrite recorded in: {rewritten}"

        statement = stage_statements[0]
        assert "csv.column_separator" in statement, statement
        assert "csv.skip_header" in statement, statement

    def test_ac3_rewritten_statement_is_redacted_in_the_audit_row(
        self, proxy_server, engine_reachable
    ):
        """The stored rewrite carries ``***``, never a credential value.

        The engine statement must carry real credentials for ``FILES()`` to
        work; the audit row must not. Asserted on structure, not on the secret
        (STANDARD SS10 rule 4).
        """
        _, port = proxy_server
        warm = _run_mysql(
            ["-e", f"SELECT * FROM @{E2E_STAGE}.{E2E_STAGE_FILE} LIMIT 1"],
            host=CLIENT_HOST,
            port=port,
            database=E2E_DATABASE,
        )
        assert warm.returncode == 0, warm.output

        for sql in _run_async(_latest_rewritten_sql(E2E_USER)):
            assert "minioadmin" not in sql, "a credential value reached the audit row"


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


class TestUserVariablesEndToEnd:
    """NOVA-25 through the wire: ``SET @x = …`` then ``SELECT @x``.

    The QA reproduction is the first test. ``SET @x`` alone always worked — it
    was the read that failed, with a message about a Nova stage.
    """

    def test_set_then_select_returns_the_value(self, proxy_server, engine_reachable):
        """The exact QA command: a marker must survive the round trip."""
        _, port = proxy_server
        result = _run_mysql(
            ["-e", "SET @x = 'QA_MARKER_12345'; SELECT @x AS val"],
            host=CLIENT_HOST,
            port=port,
            database=E2E_DATABASE,
        )

        assert result.returncode == 0, result.output
        assert [row[0] for row in result.rows()] == ["QA_MARKER_12345"]
        assert "not found" not in result.output

    def test_numeric_variable_works_in_arithmetic(self, proxy_server, engine_reachable):
        """A numeric value must splice in unquoted, so ``1 + 5`` is arithmetic.

        If the value were spliced in quoted, ``1 + '5'`` would still return 6 in
        MySQL's loose mode, so this asserts the substitution shape through the
        executor test as well; here it proves the end-to-end path works.
        """
        _, port = proxy_server
        result = _run_mysql(
            ["-e", "SET @n = 5; SELECT 1 + @n AS total"],
            host=CLIENT_HOST,
            port=port,
            database=E2E_DATABASE,
        )

        assert result.returncode == 0, result.output
        assert [row[0] for row in result.rows()] == ["6"]

    def test_variable_survives_across_queries_on_one_connection(
        self, proxy_server, engine_reachable
    ):
        """Two statements in one script share the session state."""
        _, port = proxy_server
        result = _run_mysql(
            ["-e", "SET @marker = 'kept'; SELECT @marker; SELECT @marker"],
            host=CLIENT_HOST,
            port=port,
            database=E2E_DATABASE,
        )

        assert result.returncode == 0, result.output
        assert [row[0] for row in result.rows()] == ["kept", "kept"]

    def test_unset_variable_no_longer_reports_a_nova_stage(self, proxy_server, engine_reachable):
        """An unset variable must not produce a message about a Nova feature."""
        _, port = proxy_server
        result = _run_mysql(
            ["-e", "SELECT @never_set_anywhere AS v"],
            host=CLIENT_HOST,
            port=port,
            database=E2E_DATABASE,
        )

        assert "Stage" not in result.output
        assert "not found" not in result.output

    def test_literal_that_looks_like_a_variable_is_untouched(
        self, proxy_server, engine_reachable
    ):
        """``'@x'`` is data and must not be substituted."""
        _, port = proxy_server
        result = _run_mysql(
            ["-e", "SET @x = 'substituted'; SELECT '@x' AS literal"],
            host=CLIENT_HOST,
            port=port,
            database=E2E_DATABASE,
        )

        assert result.returncode == 0, result.output
        assert [row[0] for row in result.rows()] == ["@x"]

    def test_stage_query_still_works_with_a_variable_set(self, proxy_server, engine_reachable):
        """Substitution must not break a genuine ``@stage`` reference."""
        _, port = proxy_server
        result = _run_mysql(
            [
                "-e",
                f"SET @unused = 1; SELECT * FROM @{E2E_STAGE}.{E2E_STAGE_FILE} LIMIT 1",
            ],
            host=CLIENT_HOST,
            port=port,
            database=E2E_DATABASE,
        )

        assert result.returncode == 0, result.output
        assert result.rows(), result.output


class TestColumnTypeCodes:
    """NOVA-26 through a real driver: the type code decides the Python object.

    The ``mysql`` CLI cannot see this — ``--batch`` prints every value as text,
    which is exactly why the defect survived the first round of acceptance
    testing. A DB-API driver builds its Python values from the type code, so it
    is the client that proves the fix.

    ``asyncmy`` is used rather than ``pymysql`` because it is already a runtime
    dependency of this project (``pyproject.toml``), so these tests actually run
    instead of skipping. The two drivers share the same converter contract — the
    type code in ``cursor.description`` selects the Python class — which is the
    property under test.
    """

    @staticmethod
    def _query(port: int, sql: str):
        """Run ``sql`` through asyncmy; return (description, row)."""
        import asyncmy

        async def _run():
            conn = await asyncmy.connect(
                host="127.0.0.1",
                port=port,
                user=E2E_USER,
                password=E2E_PASSWORD,
                database=E2E_DATABASE,
            )
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute(sql)
                    return cursor.description, await cursor.fetchone()
            finally:
                conn.close()

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()

    def test_decimal_keeps_its_type_code_and_python_type(self, proxy_server, engine_reachable):
        from decimal import Decimal

        _, port = proxy_server
        description, row = self._query(port, "SELECT 1.5 AS dec_col")

        assert description[0][1] == 246  # TYPE_NEWDECIMAL
        assert isinstance(row[0], Decimal)
        assert row[0] == Decimal("1.5")

    def test_decimal_supports_client_side_arithmetic(self, proxy_server, engine_reachable):
        """The business impact QA named: ``'1.5' + 1`` raises TypeError."""
        from decimal import Decimal

        _, port = proxy_server
        _, row = self._query(port, "SELECT 1.5 AS dec_col")

        assert isinstance(row[0], Decimal)
        assert row[0] + 1 == Decimal("2.5")

    def test_date_keeps_its_type_code_and_python_type(self, proxy_server, engine_reachable):
        import datetime

        _, port = proxy_server
        description, row = self._query(port, "SELECT CAST('2026-01-15' AS DATE) AS d")

        assert description[0][1] == 10  # TYPE_DATE
        assert isinstance(row[0], datetime.date)

    def test_integer_column_is_int(self, proxy_server, engine_reachable):
        _, port = proxy_server
        description, row = self._query(port, "SELECT 7 AS n")

        assert description[0][1] in (1, 2, 3, 8)  # TINY / SHORT / LONG / LONGLONG
        assert isinstance(row[0], int)
        assert row[0] == 7

    def test_double_column_is_double(self, proxy_server, engine_reachable):
        """A true DOUBLE expression must carry TYPE_DOUBLE, not VAR_STRING.

        ``2.5 + 0.5`` is *decimal* arithmetic on StarRocks and arrives as
        NEWDECIMAL — the literal spelling of an expression does not determine
        the engine's type, only its output type does. ``CAST(... AS DOUBLE)``
        and ``2/3`` are the real DOUBLE cases.
        """
        _, port = proxy_server
        description, row = self._query(port, "SELECT CAST(1.5 AS DOUBLE) AS f")

        assert description[0][1] == 5  # TYPE_DOUBLE
        assert isinstance(row[0], float)

    def test_float_division_is_double(self, proxy_server, engine_reachable):
        _, port = proxy_server
        description, row = self._query(port, "SELECT 2/3 AS f")

        assert description[0][1] == 5  # TYPE_DOUBLE
        assert isinstance(row[0], float)

    def test_mixed_numeric_row_is_consistent(self, proxy_server, engine_reachable):
        """``SELECT 1.5 AS a, 2/3 AS b`` must not disagree about types."""
        from decimal import Decimal

        _, port = proxy_server
        description, row = self._query(port, "SELECT 1.5 AS a, 2/3 AS b")

        assert description[0][1] == 246  # NEWDECIMAL
        assert description[1][1] != 253  # not VAR_STRING
        assert isinstance(row[0], Decimal)
        assert isinstance(row[1], (float, Decimal))

    def test_string_column_stays_a_string(self, proxy_server, engine_reachable):
        _, port = proxy_server
        _, row = self._query(port, "SELECT 'x' AS s")

        assert isinstance(row[0], str)

    def test_proxy_type_codes_match_the_engine(self, proxy_server, engine_reachable):
        """The same query must describe itself the same way on both ports.

        This is the comparison QA used, as an assertion: direct engine versus
        proxy, on type code and Python type. The columns cover every type
        ``_column_definition`` maps.

        Integers are compared by *Python type* rather than by exact type code.
        The proxy infers a width from the Python value and always reports
        LONGLONG, while the engine reports the narrowest type that fits (TINY
        for ``1``); both decode to ``int`` in the client, and the proxy cannot
        know the engine's declared width because ``QueryResult`` carries only
        names and values. Widening a reported width is safe — the value is what
        the client uses — so this is a documented gap rather than a defect.
        """
        from app.core.config import settings

        query = (
            "SELECT 1 AS i, 1.5 AS d, CAST(1.5 AS DOUBLE) AS f, "
            "CAST('2026-01-15' AS DATE) AS dt, 'x' AS s"
        )
        _, port = proxy_server
        engine_description, engine_row = self._query(
            settings.STARROCKS_FE_MYSQL_PORT, query
        )
        proxy_description, proxy_row = self._query(port, query)

        # Non-integer columns must match on the exact type code.
        assert proxy_description[1][1] == engine_description[1][1]  # DECIMAL
        assert proxy_description[2][1] == engine_description[2][1]  # DOUBLE
        assert proxy_description[3][1] == engine_description[3][1]  # DATE
        assert proxy_description[4][1] == engine_description[4][1]  # STRING

        # Every column must decode to the same Python class.
        assert [type(v).__name__ for v in proxy_row] == [
            type(v).__name__ for v in engine_row
        ]
        import datetime
        from decimal import Decimal

        assert isinstance(proxy_row[1], Decimal)
        assert isinstance(proxy_row[2], float)
        assert isinstance(proxy_row[3], datetime.date)
        assert isinstance(proxy_row[4], str)
