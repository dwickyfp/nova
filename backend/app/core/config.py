"""Type-safe configuration from environment variables."""

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

    # --- CORS ---
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # --- App ---
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
