"""NOVA-131: the engine stack fixture must skip, never error, when unavailable.

The Stage-4 QA gate refused to pass its "no regressions" item because a missing
Docker stack produced 25 *errors* instead of skips. These tests pin the guard so
that cannot regress:

* a busy published port held by a foreign stack is a skip reason
  (the concurrent-checkout collision),
* a failed ``docker compose up`` is a skip reason, not a propagated error,
* a missing Docker CLI is a skip reason,
* the fixture tears down a partially-created project before it skips,
* a port already owned by *our* compose project is reused, not skipped,
* ``NOVA_REQUIRE_ENGINE`` flips skips into hard failures so CI can opt out.

No Docker daemon, no sockets to a real stack, no subprocess is used: the guard
is exercised through the fixture function itself with ``_compose`` stubbed.
"""

from __future__ import annotations

import subprocess

import pytest

from tests import conftest as suite_conftest


def _run_docker_services(*, should_start: bool = False):
    """Drive the session fixture far enough to observe its skip decision.

    ``docker_services`` is a generator fixture; calling the wrapped function
    directly returns the generator and the guard runs on the first ``next``. A
    ``StopIteration`` means it reached ``yield`` (it did *not* skip).
    """
    gen = suite_conftest.docker_services.__wrapped__()
    try:
        next(gen)
    except StopIteration:
        assert should_start, "expected a skip, but the fixture started the stack"
        return
    else:
        gen.close()
        assert not should_start, "expected the fixture to start, but it skipped"
        raise AssertionError("expected a skip, but the fixture started the stack")


def _foreign_stack(monkeypatch, ports: list[str] | None = None) -> None:
    """Ports busy from someone else's stack: no compose project of ours."""
    monkeypatch.setattr(
        suite_conftest,
        "_busy_stack_ports",
        lambda: ports if ports is not None else ["StarRocks MySQL (:29030)"],
    )
    monkeypatch.setattr(suite_conftest, "_starrocks_answers", lambda: False)


def test_busy_port_is_a_skip_with_the_port_named(monkeypatch):
    _foreign_stack(monkeypatch)

    with pytest.raises(pytest.skip.Exception, match="29030"):
        _run_docker_services()


def test_compose_failure_is_a_skip_not_an_error(monkeypatch):
    _foreign_stack(monkeypatch, ports=[])
    monkeypatch.setattr(suite_conftest, "_starrocks_answers", lambda: False)

    compose_calls: list[tuple[str, ...]] = []

    def _record(*args: str) -> None:
        compose_calls.append(args)
        if args[0] == "up":
            raise subprocess.CalledProcessError(1, ["docker", "compose", "up"])

    monkeypatch.setattr(suite_conftest, "_compose", _record)

    with pytest.raises(pytest.skip.Exception, match="compose up failed"):
        _run_docker_services()

    # The failed `up` is followed by a scoped teardown so a partially-created
    # project cannot poison the next run.
    assert ("down", "-v") in compose_calls


def test_missing_docker_cli_is_a_skip(monkeypatch):
    _foreign_stack(monkeypatch, ports=[])

    def _missing(*args: str) -> None:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(suite_conftest, "_compose", _missing)

    with pytest.raises(pytest.skip.Exception, match="Docker CLI not found"):
        _run_docker_services()


def test_require_engine_turns_a_skip_into_a_failure(monkeypatch):
    monkeypatch.setattr(suite_conftest, "_REQUIRE_ENGINE", True)
    _foreign_stack(monkeypatch, ports=["Redis (:26379)"])

    with pytest.raises(pytest.fail.Exception, match="NOVA_REQUIRE_ENGINE"):
        _run_docker_services()


def test_our_running_stack_is_reused_not_skipped(monkeypatch):
    """CI starts the stack before pytest; the gate must not skip it as a collision."""
    monkeypatch.setattr(suite_conftest, "_busy_stack_ports", lambda: ["StarRocks MySQL (:29030)"])
    monkeypatch.setattr(suite_conftest, "_starrocks_answers", lambda: True)
    compose_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(suite_conftest, "_compose", lambda *a: compose_calls.append(a))

    gen = suite_conftest.docker_services.__wrapped__()
    next(gen)
    with pytest.raises(StopIteration):
        next(gen)

    assert compose_calls == [], "an already-running stack must not be restarted"


def test_healthy_stack_starts_and_tears_down(monkeypatch):
    _foreign_stack(monkeypatch, ports=[])
    monkeypatch.setattr(suite_conftest, "time", _StubTime())
    compose_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(suite_conftest, "_compose", lambda *a: compose_calls.append(a))

    gen = suite_conftest.docker_services.__wrapped__()
    next(gen)
    with pytest.raises(StopIteration):
        next(gen)

    assert compose_calls == [("up", "-d", "--wait"), ("down", "-v")]


class _StubTime:
    """Skip the fixture's fixed post-start settle sleep."""

    def sleep(self, _seconds: float) -> None:
        return None
