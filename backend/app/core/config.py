"""Type-safe configuration from environment variables."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

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

    # --- Redis (session store) ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Security ---
    SECRET_KEY: str = "change-me-in-production-use-openssl-rand-hex-32"
    FERNET_KEY: str = ""  # Generated: Fernet.generate_key()
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    SESSION_TTL_SECONDS: int = 3600

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


def get_storage_connection(name: str) -> StorageConnectionConfig:
    config = load_nova_app_config()
    if name in config.storage_connections:
        return config.storage_connections[name]
    return next(iter(config.storage_connections.values()))
