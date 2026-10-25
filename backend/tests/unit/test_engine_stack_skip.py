"""NOVA-131: an unavailable Docker stack must skip, never fail.

The behaviour under test is a property of the *test harness*, not of the app:
``tests/conftest.py`` never raises when the stack cannot start, so
engine-marked tests come out of a stack-less run as skips. These tests drive
the real fixtures through a simulated environment (no Docker / busy ports /
a failing compose) rather than requiring one on the machine running them.
"""

from __future__ import annotations

import subprocess

import pytest

from tests import conftest as stack_conftest


def _port_busy(*busy_ports: int):
    """A ``_port_in_use`` replacement that reports exactly ``busy_ports``."""

    def _probe(port: int) -> bool:
        return port in busy_ports

    return _probe


class TestPreflightFailure:
    """``_preflight_failure`` names only environment prerequisites.

    A busy port is deliberately NOT a failure here: the fixture distinguishes a
    reusable stack from a foreign holder afterward (see the reuse/collision
    tests below).
    """

    def test_no_docker_binary_is_reported(self, monkeypatch):
        monkeypatch.setattr(stack_conftest.shutil, "which", lambda _: None)
        assert stack_conftest._preflight_failure() == "docker is not installed"

    def test_daemon_down_is_reported(self, monkeypatch):
        monkeypatch.setattr(stack_conftest.shutil, "which", lambda _: "/usr/bin/docker")

        def _fail(*args, **kwargs):
            return subprocess.CompletedProcess(args, returncode=1)

        monkeypatch.setattr(stack_conftest.subprocess, "run", _fail)
        assert stack_conftest._preflight_failure() == "docker daemon is not running"

    def test_a_busy_port_is_not_a_preflight_failure(self, monkeypatch):
        """The CI case: every port bound, all answering — reusable, not a reason."""
        monkeypatch.setattr(stack_conftest.shutil, "which", lambda _: "/usr/bin/docker")
        monkeypatch.setattr(
            stack_conftest.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a, returncode=0),
        )
        monkeypatch.setattr(
            stack_conftest,
            "_port_in_use",
            _port_busy(*stack_conftest.engine_host_ports().values()),
        )
        assert stack_conftest._preflight_failure() is None

    def test_a_free_environment_has_no_reason(self, monkeypatch):
        monkeypatch.setattr(stack_conftest.shutil, "which", lambda _: "/usr/bin/docker")
        monkeypatch.setattr(
            stack_conftest.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a, returncode=0),
        )
        monkeypatch.setattr(stack_conftest, "_port_in_use", _port_busy())
        assert stack_conftest._preflight_failure() is None


class TestStackReuseAndCollision:
    """Busy ports split into "reuse the running stack" and "foreign holder"."""

    def test_all_ports_busy_and_answering_means_reuse(self, monkeypatch):
        ports = stack_conftest.engine_host_ports().values()
        monkeypatch.setattr(stack_conftest, "_port_in_use", _port_busy(*ports))
        assert stack_conftest._stack_already_reachable() is True
        assert stack_conftest._partial_collision_failure() is None

    def test_partial_busy_is_a_collision_with_the_stuck_port(self, monkeypatch):
        # Only the FE port is held -> compose cannot bind it.
        fe_port = stack_conftest.engine_host_ports()["starrocks-fe"]
        monkeypatch.setattr(
            stack_conftest, "_port_in_use", _port_busy(fe_port)
        )
        assert stack_conftest._stack_already_reachable() is False
        reason = stack_conftest._partial_collision_failure()
        assert reason is not None
        assert str(fe_port) in reason

    def test_free_ports_are_neither_reuse_nor_collision(self, monkeypatch):
        monkeypatch.setattr(stack_conftest, "_port_in_use", _port_busy())
        assert stack_conftest._stack_already_reachable() is False
        assert stack_conftest._partial_collision_failure() is None


class TestStackStatus:
    def test_skip_reason_is_derived_from_the_status(self):
        assert stack_conftest.StackStatus().unavailable is False
        assert stack_conftest.StackStatus(reason="docker is not installed").unavailable

    def test_require_stack_skips_with_the_reason(self):
        status = stack_conftest.StackStatus(reason="docker is not installed")
        with pytest.raises(pytest.skip.Exception) as excinfo:
            stack_conftest.require_stack(status)
        assert "docker is not installed" in str(excinfo.value)

    def test_require_stack_is_a_no_op_when_the_stack_is_up(self):
        stack_conftest.require_stack(stack_conftest.StackStatus())


class TestDockerServicesIsSkipFriendly:
    """The fixture itself must return a status, never raise, without Docker."""

    def test_unavailable_stack_yields_a_status_not_an_error(self, monkeypatch):
        monkeypatch.setattr(stack_conftest.shutil, "which", lambda _: None)
        generator = stack_conftest.docker_services.__wrapped__()
        status = next(generator)
        assert status.unavailable
        assert "docker is not installed" in status.reason
        generator.close()

    def test_failed_compose_up_is_captured_as_a_reason(self, monkeypatch):
        monkeypatch.setattr(stack_conftest.shutil, "which", lambda _: "/usr/bin/docker")
        monkeypatch.setattr(
            stack_conftest.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a, returncode=0),
        )
        # Ports free and no engine running, so the fixture owns the `up` it runs.
        monkeypatch.setattr(stack_conftest, "_port_in_use", _port_busy())
        monkeypatch.setattr(stack_conftest, "_running_engine", lambda: None)

        def _boom(*args, **kwargs):
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=["docker", "compose", "up"],
                stderr="Bind for 0.0.0.0:29030 failed: port is already allocated",
            )

        monkeypatch.setattr(stack_conftest, "_compose", _boom)
        generator = stack_conftest.docker_services.__wrapped__()
        status = next(generator)
        assert status.unavailable
        assert "port is already allocated" in status.reason
        generator.close()

    def test_healthy_stack_is_reported_as_available(self, monkeypatch):
        """The success path: a bound, answering stack must NOT be misread.

        Regression for the inverted post-up check (NOVA-131 QA): after a real
        ``up --wait`` every published port is *in use*, which is what success
        looks like. Stubbing the ports as listening and compose as a no-op must
        therefore yield ``unavailable is False`` — the old code treated an
        in-use port as "unreachable" and skipped the whole engine suite.
        """
        monkeypatch.setattr(stack_conftest.shutil, "which", lambda _: "/usr/bin/docker")
        monkeypatch.setattr(
            stack_conftest.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a, returncode=0),
        )
        # Free before `up` (preflight passes) and no engine running…
        monkeypatch.setattr(stack_conftest, "_port_in_use", _port_busy())
        monkeypatch.setattr(stack_conftest, "_running_engine", lambda: None)
        calls: list[tuple[str, ...]] = []

        def _noop_compose(*args, **kwargs):
            calls.append(args)
            # …and bound afterwards, because the stack we just started owns them.
            monkeypatch.setattr(
                stack_conftest,
                "_port_in_use",
                _port_busy(*stack_conftest.engine_host_ports().values()),
            )
            return subprocess.CompletedProcess(args, returncode=0)

        monkeypatch.setattr(stack_conftest, "_compose", _noop_compose)
        monkeypatch.setattr(stack_conftest.time, "sleep", lambda _: None)

        generator = stack_conftest.docker_services.__wrapped__()
        status = next(generator)
        assert not status.unavailable, status.reason
        assert status.owned
        generator.close()
        assert ("up", "-d", "--wait") in calls

    def test_reachable_prestarted_stack_is_reused_not_skipped(self, monkeypatch):
        """The CI path: CI starts the stack before pytest, so every port is
        already bound when the fixture runs.

        That must yield an *available* status with no compose call at all — no
        ``up`` (the ports are taken) and, crucially, no ``down`` (the fixture
        does not own the stack). Treating the busy ports as a collision made 25
        L3 tests skip silently in CI while the job stayed green.
        """
        monkeypatch.setattr(stack_conftest.shutil, "which", lambda _: "/usr/bin/docker")
        monkeypatch.setattr(
            stack_conftest.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a, returncode=0),
        )
        # Bound and answering from the very start — CI's already-running stack.
        monkeypatch.setattr(
            stack_conftest,
            "_port_in_use",
            _port_busy(*stack_conftest.engine_host_ports().values()),
        )
        calls: list[tuple[str, ...]] = []
        monkeypatch.setattr(
            stack_conftest,
            "_compose",
            lambda *args, **kwargs: calls.append(args) or subprocess.CompletedProcess(args, 0),
        )
        monkeypatch.setattr(stack_conftest.time, "sleep", lambda _: None)

        generator = stack_conftest.docker_services.__wrapped__()
        status = next(generator)
        assert not status.unavailable, status.reason
        assert not status.owned
        generator.close()
        assert calls == [], f"must not touch compose when reusing a stack: {calls}"

    def test_stack_that_does_not_answer_is_reported_unavailable(self, monkeypatch):
        """The genuine post-up failure: a published port that refuses a connect."""

        def _compose_up_then_nothing(*args, **kwargs):
            return subprocess.CompletedProcess(args, returncode=0)

        monkeypatch.setattr(stack_conftest, "_compose", _compose_up_then_nothing)
        monkeypatch.setattr(stack_conftest, "_preflight_failure", lambda: None)
        monkeypatch.setattr(stack_conftest.time, "sleep", lambda _: None)
        # Empty before `up` via the preflight check, then still refusing after.
        monkeypatch.setattr(stack_conftest, "_port_in_use", _port_busy())
        monkeypatch.setattr(stack_conftest, "_running_engine", lambda: None)

        generator = stack_conftest.docker_services.__wrapped__()
        status = next(generator)
        assert status.unavailable
        assert "do not answer" in status.reason
        generator.close()


class TestReachabilityProbe:
    """`_busy_ports` and `_unreachable_ports` are opposites and must stay so."""

    def test_a_listening_port_is_busy_but_not_unreachable(self, monkeypatch):
        monkeypatch.setattr(
            stack_conftest, "_port_in_use", _port_busy(*stack_conftest.engine_host_ports().values())
        )
        assert len(stack_conftest._busy_ports()) == len(stack_conftest.engine_host_ports())
        assert stack_conftest._unreachable_ports() == []

    def test_a_refused_port_is_unreachable_but_not_busy(self, monkeypatch):
        monkeypatch.setattr(stack_conftest, "_port_in_use", _port_busy())
        assert stack_conftest._busy_ports() == []
        assert len(stack_conftest._unreachable_ports()) == len(
            stack_conftest.engine_host_ports()
        )

    def test_probe_reflects_a_real_listening_socket(self):
        """No stubbing: bind a socket and check both probes read it correctly."""
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            assert stack_conftest._port_in_use(port) is True

        assert stack_conftest._port_in_use(port) is False


class TestPortOverrides:
    def test_defaults_match_the_historical_ports(self, monkeypatch):
        for env in stack_conftest.PORT_ENV.values():
            monkeypatch.delenv(env, raising=False)
        assert stack_conftest.engine_host_ports() == stack_conftest.PORT_DEFAULTS

    def test_env_overrides_move_the_ports(self, monkeypatch):
        monkeypatch.setenv("NOVA_TEST_FE_MYSQL_PORT", "39030")
        monkeypatch.setenv("NOVA_TEST_MINIO_PORT", "39000")
        ports = stack_conftest.engine_host_ports()
        assert ports["starrocks-fe"] == 39030
        assert ports["minio"] == 39000

    def test_running_engine_elsewhere_is_never_duplicated(self, monkeypatch):
        """A StarRocks FE on other ports (the dev stack) blocks a second stack.

        Two FEs on one machine exhaust the Docker disk and both stop, so the
        fixture must name the running container and skip instead of `up`.
        """
        monkeypatch.setattr(stack_conftest, "_preflight_failure", lambda: None)
        monkeypatch.setattr(stack_conftest, "_port_in_use", _port_busy())
        monkeypatch.setattr(stack_conftest, "_running_engine", lambda: "nova-starrocks-fe")
        calls: list[tuple[str, ...]] = []
        monkeypatch.setattr(
            stack_conftest,
            "_compose",
            lambda *args, **kwargs: calls.append(args) or subprocess.CompletedProcess(args, 0),
        )

        generator = stack_conftest.docker_services.__wrapped__()
        status = next(generator)
        assert status.unavailable
        assert "nova-starrocks-fe" in status.reason
        assert "NOVA_TEST_ROOT_PASSWORD" in status.reason
        generator.close()
        assert calls == []

    def test_running_engine_reads_docker_ps(self, monkeypatch):
        listed = subprocess.CompletedProcess([], 0, stdout="nova-starrocks-fe\n")
        monkeypatch.setattr(stack_conftest.subprocess, "run", lambda *a, **k: listed)
        assert stack_conftest._running_engine() == "nova-starrocks-fe"
        failed = subprocess.CompletedProcess([], 1, stdout=None)
        monkeypatch.setattr(stack_conftest.subprocess, "run", lambda *a, **k: failed)
        assert stack_conftest._running_engine() is None


class TestStackCredentials:
    """Credentials come from the environment, defaulting to the compose stack."""

    def test_defaults_match_the_compose_test_stack(self, monkeypatch):
        for name in (
            "NOVA_TEST_ROOT_PASSWORD",
            "NOVA_TEST_S3_ACCESS_KEY",
            "NOVA_TEST_S3_SECRET_KEY",
            "NOVA_TEST_REDIS_PASSWORD",
            "NOVA_TEST_REDIS_PORT",
        ):
            monkeypatch.delenv(name, raising=False)
        assert stack_conftest.root_password() == ""
        assert stack_conftest.s3_credentials() == ("minioadmin", "minioadmin")
        assert stack_conftest.redis_url() == "redis://127.0.0.1:26379/0"

    def test_a_reused_stack_supplies_its_own(self, monkeypatch):
        monkeypatch.setenv("NOVA_TEST_ROOT_PASSWORD", "root-secret")
        monkeypatch.setenv("NOVA_TEST_S3_ACCESS_KEY", "access")
        monkeypatch.setenv("NOVA_TEST_S3_SECRET_KEY", "secret")
        monkeypatch.setenv("NOVA_TEST_REDIS_PASSWORD", "redis-secret")
        monkeypatch.setenv("NOVA_TEST_REDIS_PORT", "6379")
        assert stack_conftest.root_password() == "root-secret"
        assert stack_conftest.s3_credentials() == ("access", "secret")
        assert stack_conftest.redis_url() == "redis://:redis-secret@127.0.0.1:6379/0"
        assert stack_conftest.redis_url(port=7000, db=2) == "redis://:redis-secret@127.0.0.1:7000/2"
