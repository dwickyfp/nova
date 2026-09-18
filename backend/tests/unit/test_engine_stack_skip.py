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
    """``_preflight_failure`` names the reason before compose is ever invoked."""

    def test_no_docker_binary_is_reported(self, monkeypatch):
        monkeypatch.setattr(stack_conftest.shutil, "which", lambda _: None)
        assert stack_conftest._preflight_failure() == "docker is not installed"

    def test_daemon_down_is_reported(self, monkeypatch):
        monkeypatch.setattr(stack_conftest.shutil, "which", lambda _: "/usr/bin/docker")

        def _fail(*args, **kwargs):
            return subprocess.CompletedProcess(args, returncode=1)

        monkeypatch.setattr(stack_conftest.subprocess, "run", _fail)
        assert stack_conftest._preflight_failure() == "docker daemon is not running"

    def test_busy_port_is_reported_with_the_port(self, monkeypatch):
        monkeypatch.setattr(stack_conftest.shutil, "which", lambda _: "/usr/bin/docker")
        monkeypatch.setattr(
            stack_conftest.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a, returncode=0),
        )
        monkeypatch.setattr(
            stack_conftest, "_port_in_use", _port_busy(29030, 26379)
        )
        reason = stack_conftest._preflight_failure()
        assert reason is not None
        assert "29030" in reason
        assert "26379" in reason

    def test_a_free_environment_has_no_reason(self, monkeypatch):
        monkeypatch.setattr(stack_conftest.shutil, "which", lambda _: "/usr/bin/docker")
        monkeypatch.setattr(
            stack_conftest.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a, returncode=0),
        )
        monkeypatch.setattr(stack_conftest, "_port_in_use", _port_busy())
        assert stack_conftest._preflight_failure() is None


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
        monkeypatch.setattr(stack_conftest, "_port_in_use", _port_busy())

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
        # Free before `up` (preflight passes)…
        monkeypatch.setattr(stack_conftest, "_port_in_use", _port_busy())
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
        generator.close()
        assert ("up", "-d", "--wait") in calls

    def test_stack_that_does_not_answer_is_reported_unavailable(self, monkeypatch):
        """The genuine post-up failure: a published port that refuses a connect."""

        def _compose_up_then_nothing(*args, **kwargs):
            return subprocess.CompletedProcess(args, returncode=0)

        monkeypatch.setattr(stack_conftest, "_compose", _compose_up_then_nothing)
        monkeypatch.setattr(stack_conftest.time, "sleep", lambda _: None)
        # Empty before `up` via the preflight check, then still refusing after.
        monkeypatch.setattr(stack_conftest, "_port_in_use", _port_busy())

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
