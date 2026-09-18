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
