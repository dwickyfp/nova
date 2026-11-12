"""The real shell launcher against disposable host processes and fake tooling."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def launcher(tmp_path):
    root = tmp_path / "checkout with spaces"
    (root / "backend" / "scripts").mkdir(parents=True)
    (root / "backend" / ".env").write_text("")
    (root / "frontend").mkdir()
    (root / "dev.sh").write_bytes((ROOT / "dev.sh").read_bytes())
    binaries = tmp_path / "bin"
    binaries.mkdir()
    uv = binaries / "uv"
    uv.write_text(f"#!{sys.executable}\n" + '''
import os, signal, sys, time
args = sys.argv[1:]
if args[-1:] == ['-']:
    sys.stdin.read()
    raise SystemExit(int(os.environ.get('FAKE_CONFIG_FAILURE', '0')))
if any('dev_readiness.py' in arg for arg in args):
    raise SystemExit(int(os.environ.get('FAKE_NOT_READY', '0')))
with open(os.environ['CHILD_RECORD'], 'a') as handle:
    handle.write(str(os.getpid()) + '\\n')
def stop(*_):
    time.sleep(float(os.environ.get('FAKE_DRAIN_SECONDS', '0')))
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
if os.environ.get('FAKE_CRASH') == '1' and os.environ.get('NOVA_PROCESS_ROLE') == 'query':
    time.sleep(0.4)
    raise SystemExit(7)
while True:
    time.sleep(0.1)
''')
    uv.chmod(0o700)
    docker = binaries / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n")
    docker.chmod(0o700)
    environment = {
        **os.environ, "PATH": f"{binaries}:{os.environ['PATH']}",
        "RANGER_PASSWORD": "fixture", "SUPERSET_SERVICE_TOKEN": "fixture",
        "CHILD_RECORD": str(tmp_path / "children"), "DEV_STARTUP_SECONDS": "1",
    }
    names = ["BACKEND_PORT", "QUERY_PORT", "PROXY_PORT"] + [
        f"NOVA_METRICS_{name}_PORT" for name in (
            "SCHEDULER", "WORKER", "PROXY", "INGESTION_WORKER", "ML_WORKER",
            "NOTEBOOK_WORKER", "AUTOMATION_WORKER", "AUTOPILOT_WORKER", "MAINTENANCE_WORKER",
            "OUTBOX_WORKER",
        )
    ]
    ports = []
    for name in names:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        ports.append(sock)
        environment[name] = str(sock.getsockname()[1])
    for sock in ports:
        sock.close()
    return root, environment


def start(launcher, **environment):
    root, base = launcher
    return subprocess.Popen(
        ["/bin/bash", str(root / "dev.sh"), "--no-frontend", "--no-infra-check"],
        env={**base, **environment}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True,
    )


def no_survivors(launcher):
    record = Path(launcher[1]["CHILD_RECORD"])
    assert record.exists()
    for pid in record.read_text().splitlines():
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid), 0)


@pytest.mark.parametrize("environment", [{"FAKE_CRASH": "1"}, {"FAKE_NOT_READY": "1"}])
def test_startup_failure_is_nonzero_and_reaps_every_process(launcher, environment):
    process = start(launcher, **environment)
    try:
        output, _ = process.communicate(timeout=20)
        assert process.returncode != 0, output
        no_survivors(launcher)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def test_shutdown_allows_worker_drain_beyond_five_seconds(launcher):
    process = start(launcher, FAKE_DRAIN_SECONDS="6")
    try:
        assert process.stdout is not None
        deadline = time.monotonic() + 15
        for line in process.stdout:
            if "all Nova runtimes are ready" in line:
                break
            assert time.monotonic() < deadline
        assert process.poll() is None
        began = time.monotonic()
        process.terminate()
        output, _ = process.communicate(timeout=20)
        assert process.returncode == 143, output
        assert time.monotonic() - began >= 6
        no_survivors(launcher)
        for log in (launcher[0] / ".dev-logs").glob("*.log"):
            assert log.stat().st_mode & 0o077 == 0
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def test_configuration_refusal_exits_cleanly_before_spawning_children(launcher):
    process = start(launcher, FAKE_CONFIG_FAILURE="1")
    try:
        output, _ = process.communicate(timeout=10)
        assert process.returncode == 1, output
        assert "runtime configuration is incomplete" in output
        assert "no application processes were started" in output
        assert "unbound variable" not in output
        assert not Path(launcher[1]["CHILD_RECORD"]).exists()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def test_duplicate_ports_refused_before_any_child_starts(launcher):
    root, environment = launcher
    environment["QUERY_PORT"] = environment["BACKEND_PORT"]
    result = subprocess.run(
        ["/bin/bash", str(root / "dev.sh"), "--no-frontend", "--no-infra-check"],
        env=environment, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "more than one process" in result.stdout + result.stderr
    assert not Path(environment["CHILD_RECORD"]).exists()
