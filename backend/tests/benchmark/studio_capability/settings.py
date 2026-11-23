"""Where the benchmark runs and as whom. Secrets come from the environment only.

=============================  ==============================================
``NOVA_BENCH_API``             Nova base URL (default ``http://127.0.0.1:8000``)
``NOVA_BENCH_UI``              Studio web URL for screenshots (default
                               ``http://127.0.0.1:5173``)
``NOVA_BENCH_USER``            The person under test (default ``alice``)
``NOVA_BENCH_PASSWORD``        Their password (required)
``NOVA_BENCH_ROLE``            Their active role (default ``marketing``)
``NOVA_BENCH_ADMIN_USER``      Ground-truth administrator (default ``nova_admin``)
``NOVA_BENCH_ADMIN_PASSWORD``  Its password; falls back to
                               ``NOVA_ADMIN_TEST_PASSWORD`` in ``docker/.env``
``NOVA_BENCH_AGENT``           Agent name (default ``Sales Analyst``)
``NOVA_BENCH_OUT``             Results directory, outside the repository
``NOVA_BENCH_JUDGE_*``         Judge endpoint: ``BASE_URL``, ``API_KEY``,
                               ``MODEL`` (OpenAI-compatible chat completions)
=============================  ==============================================
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]


def _env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Identity:
    username: str
    password: str
    role: str | None

    def __repr__(self) -> str:  # never print the password
        return f"Identity({self.username!r}, role={self.role!r})"


def api() -> str:
    return os.environ.get("NOVA_BENCH_API", "http://127.0.0.1:8000").rstrip("/")


def ui() -> str:
    return os.environ.get("NOVA_BENCH_UI", "http://127.0.0.1:5173").rstrip("/")


def agent_name() -> str:
    return os.environ.get("NOVA_BENCH_AGENT", "Sales Analyst")


def user() -> Identity:
    password = os.environ.get("NOVA_BENCH_PASSWORD")
    if not password:
        raise SystemExit("Set NOVA_BENCH_PASSWORD for the user under test.")
    return Identity(os.environ.get("NOVA_BENCH_USER", "alice"), password,
                    os.environ.get("NOVA_BENCH_ROLE", "marketing"))


def admin() -> Identity:
    password = os.environ.get("NOVA_BENCH_ADMIN_PASSWORD") or _env_file(
        Path(os.environ.get("NOVA_BENCH_ENV_FILE", REPO / "docker" / ".env"))
    ).get("NOVA_ADMIN_TEST_PASSWORD")
    if not password:
        raise SystemExit("Set NOVA_BENCH_ADMIN_PASSWORD (or NOVA_BENCH_ENV_FILE).")
    return Identity(os.environ.get("NOVA_BENCH_ADMIN_USER", "nova_admin"), password,
                    os.environ.get("NOVA_BENCH_ADMIN_ROLE", "ACCOUNTADMIN"))


def out_dir(explicit: str | None = None) -> Path:
    raw = explicit or os.environ.get("NOVA_BENCH_OUT")
    if not raw:
        raise SystemExit("Set --out or NOVA_BENCH_OUT to a directory outside the repository.")
    path = Path(raw).expanduser().resolve()
    if path == REPO or REPO in path.parents:
        raise SystemExit("Benchmark results must be written outside the repository.")
    path.mkdir(parents=True, exist_ok=True)
    return path
