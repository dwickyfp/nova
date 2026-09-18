"""Type-safe configuration from environment variables."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Nova backend configuration. All values from env vars or .env file."""

    # --- StarRocks ---
    STARROCKS_HOST: str = "localhost"
    STARROCKS_FE_MYSQL_PORT: int = 9030
    STARROCKS_HTTP_PORT: int = 8030
    STARROCKS_ROOT_USER: str = "root"
    STARROCKS_ROOT_PASSWORD: str = ""

    # --- MySQL protocol proxy ---
    # Values mirror the ``proxy:`` block in docker/nova.yaml; the defaults here
    # are what the embedded lifespan uses when nothing overrides them.
    PROXY_ENABLED: bool = True
    PROXY_HOST: str = "0.0.0.0"
    PROXY_PORT: int = 4406
    PROXY_MAX_CONNECTIONS: int = 100
    PROXY_CONNECT_TIMEOUT: int = 10
    PROXY_READ_TIMEOUT: int = 300

    # --- Redis (session store) ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Task orchestration: scheduler process (nova-scheduler) ---
    # The scheduler is a standalone process (`python -m app.scheduler`); the
    # web process never runs it. All values live here so both processes read
    # the same source of truth.
    SCHEDULER_POLL_INTERVAL_SECONDS: float = 15.0
    SCHEDULER_LEADER_LOCK_KEY: str = "nova:scheduler:leader"
    SCHEDULER_LEADER_LOCK_TTL_SECONDS: int = 60
    # The StarRocks session timezone of the scheduler's system connection. Nova
    # writes ``NOW()`` values (e.g. ``created_at``) in this zone and reads them
    # back naive. Empty means "ask the engine" via ``SELECT @@time_zone``, which
    # is the only answer that cannot drift from the deployment. Set it only to
    # override (tests, unusual setups); never hardcode UTC.
    SCHEDULER_ENGINE_TIMEZONE: str = ""

    # --- Task orchestration: Redis Streams transport (nova-scheduler → workers) ---
    # Redis is ephemeral transport only; NOVA_SYSTEM is the source of truth.
    TASK_STREAM_KEY: str = "nova:tasks:graph_runs"
    TASK_STREAM_GROUP: str = "nova-workers"
    TASK_STREAM_MAXLEN: int = 10000

    # --- Task orchestration: worker process (nova-worker) ---
    # The worker consumes graph runs and executes nodes as their owner. The
    # reconciler re-derives work the stream lost (Redis flush, worker death);
    # a RUNNING row whose heartbeat is older than the abandon window is treated
    # as abandoned and re-evaluated, never trusted (design §2, rule 3).
    WORKER_NAME: str = "nova-worker"
    WORKER_TASK_POLL_INTERVAL_SECONDS: float = 1.0
    #: A node waits this long for its native TaskRun before being failed. The
    #: engine's own `task_runs_timeout_second` (4 h) is the hard ceiling.
    WORKER_TASK_POLL_TIMEOUT_SECONDS: float = 14400.0
    #: How long a RUNNING node may go without a heartbeat before the reconciler
    #: abandons and re-evaluates it.
    WORKER_HEARTBEAT_TIMEOUT_SECONDS: int = 120
    WORKER_RECONCILE_INTERVAL_SECONDS: float = 30.0
    #: How many graph runs a worker reads from the stream per drain.
    WORKER_STREAM_BATCH_SIZE: int = 10
    #: Nova's default for the engine's ``max_task_consecutive_fail_count`` (10).
    #: The reconciler prefers the engine's live value from
    #: ``ADMIN SHOW FRONTEND CONFIG`` and falls back to this when the engine's
    #: FE-config surface is unavailable.
    WORKER_MAX_CONSECUTIVE_FAIL_COUNT: int = 10

    # --- Migration connector (Phase 11) ---
    # Path to the operator-provided ``starrocks-cluster-sync`` binary. Nova
    # never bundles or redistributes it (its license is undeclared), so the
    # operator installs it and points this at the executable. Empty means "not
    # configured"; the connector reports a typed, non-fatal error rather than
    # guessing a location. Execute is gated on #7 and does not run here.
    MIGRATION_CLUSTER_SYNC_BINARY: str = ""

    # --- Security ---
    SECRET_KEY: str = "change-me-in-production-use-openssl-rand-hex-32"
    FERNET_KEY: str = ""  # Generated: Fernet.generate_key()
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    SESSION_TTL_SECONDS: int = 3600

    # --- Internal machine-to-machine channel (/api/v1/internal/*) ---
    # Pre-shared secret for callers with no Nova session (e.g. the Java UDF
    # bridge). No default on purpose: unset means the internal endpoints fail
    # closed (503). Configure via env/secret manager, never commit a value.
    NOVA_INTERNAL_TOKEN: str = ""
    #: Comma-separated peer addresses allowed to reach internal endpoints in
    #: addition to loopback. Empty means loopback only.
    NOVA_INTERNAL_TRUSTED_PROXY: str = ""

    # --- MinIO / S3 (default storage) ---
    S3_ENDPOINT: str = "http://localhost:9000"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET: str = "nova-stages"
    NOVA_CONFIG_PATH: str = str(
        Path(__file__).resolve().parents[3] / "docker" / "nova.yaml"
    )

    # --- CORS ---
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # --- App ---
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()


@dataclass(frozen=True)
class StorageConnectionConfig:
    name: str
    type: str
    endpoint: str
    bucket: str
    access_key: str
    secret_key: str
    region: str = ""
    path_style: bool = True
    ssl: bool = False
    #: Optional external secret reference. When set, `access_key`/`secret_key`
    #: here are placeholders only and are never used to authenticate: the
    #: value is fetched from the provider named by the reference. Reference-only
    #: by design — Nova persists the reference, never the value (NOVA-58).
    secret_ref: str = ""


@dataclass(frozen=True)
class WorkspaceStorageConfig:
    storage_connection: str = "production"
    base_prefix: str = "workspaces"


@dataclass(frozen=True)
class NovaAppConfig:
    storage_connections: dict[str, StorageConnectionConfig]
    workspace: WorkspaceStorageConfig


_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _substitute_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda match: os.getenv(match.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


def _resolve_endpoint(endpoint: str) -> str:
    if not endpoint:
        return endpoint

    parsed = urlparse(endpoint)
    if parsed.hostname not in {"minio"}:
        return endpoint

    # When backend runs on the host machine, docker-internal names like
    # `minio` are not resolvable. Prefer the host-side env endpoint if present.
    if settings.S3_ENDPOINT:
        return settings.S3_ENDPOINT
    return endpoint


def to_docker_endpoint(endpoint: str) -> str:
    """Convert a host-side endpoint back to docker-internal for StarRocks.

    e.g. http://127.0.0.1:9000 → http://minio:9000
    StarRocks runs inside Docker and can only resolve docker-internal hostnames.
    """
    if not endpoint:
        return endpoint
    parsed = urlparse(endpoint)
    # Map localhost/127.0.0.1 → minio (the Docker service name)
    if parsed.hostname in {"127.0.0.1", "localhost", "0.0.0.0"}:
        port = parsed.port or 9000
        return f"{parsed.scheme}://minio:{port}"
    return endpoint


@lru_cache(maxsize=1)
def load_nova_app_config() -> NovaAppConfig:
    path = Path(settings.NOVA_CONFIG_PATH)
    if not path.exists():
        return NovaAppConfig(
            storage_connections={
                "production": StorageConnectionConfig(
                    name="production",
                    type="minio",
                    endpoint=settings.S3_ENDPOINT,
                    bucket=settings.S3_BUCKET,
                    access_key=settings.S3_ACCESS_KEY,
                    secret_key=settings.S3_SECRET_KEY,
                )
            },
            workspace=WorkspaceStorageConfig(),
        )

    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    parsed = _substitute_env(raw)
    storage_connections = {}
    for name, cfg in parsed.get("storage", {}).get("connections", {}).items():
        storage_connections[name] = StorageConnectionConfig(
            name=name,
            type=cfg.get("type", "minio"),
            endpoint=_resolve_endpoint(cfg.get("endpoint", "")) or settings.S3_ENDPOINT,
            bucket=cfg.get("bucket", "") or settings.S3_BUCKET,
            access_key=cfg.get("access_key", "") or settings.S3_ACCESS_KEY,
            secret_key=cfg.get("secret_key", "") or settings.S3_SECRET_KEY,
            region=cfg.get("region", ""),
            path_style=bool(cfg.get("path_style", True)),
            ssl=bool(cfg.get("ssl", False)),
            secret_ref=cfg.get("secret_ref", "") or "",
        )

    if not storage_connections:
        storage_connections["production"] = StorageConnectionConfig(
            name="production",
            type="minio",
            endpoint=settings.S3_ENDPOINT,
            bucket=settings.S3_BUCKET,
            access_key=settings.S3_ACCESS_KEY,
            secret_key=settings.S3_SECRET_KEY,
        )

    workspace_cfg = parsed.get("workspace", {})
    workspace = WorkspaceStorageConfig(
        storage_connection=workspace_cfg.get("storage_connection", "production"),
        base_prefix=workspace_cfg.get("base_prefix", "workspaces"),
    )
    return NovaAppConfig(
        storage_connections=storage_connections,
        workspace=workspace,
    )


def get_storage_connection(name: str | None) -> StorageConnectionConfig:
    """Look up a connection by name, falling back to the workspace default.

    ``None`` (or an unknown name) resolves to the default connection, which is
    what callers that have no stage context want.
    """
    config = load_nova_app_config()
    if name is not None and name in config.storage_connections:
        return config.storage_connections[name]
    return next(iter(config.storage_connections.values()))
