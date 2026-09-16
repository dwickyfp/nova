"""Credential injection for FILES() function calls.

Loads storage credentials from config and injects them into SQL
so users never see or type storage credentials.

Credentials come from `nova.yaml` / env via `app.core.config`; there is no
hardcoded fallback. When the storage connection is not configured the parameter
set is returned without credentials, so the FILES() call fails loudly instead of
silently authenticating with a well-known default.
"""

from app.core.config import get_storage_connection, load_nova_app_config, settings


def resolve_storage_credentials(storage_connection: str | None = None) -> tuple[str, str]:
    """Resolve access_key/secret_key for a storage connection from config.

    Falls back to the workspace's default storage connection. Values may be
    empty when nothing is configured — callers must not substitute defaults.
    """
    config = load_nova_app_config()
    name = storage_connection or config.workspace.storage_connection
    return get_storage_connection(name).access_key, get_storage_connection(name).secret_key


def get_credential_params(
    storage_type: str = "s3", storage_connection: str | None = None
) -> dict[str, str]:
    """Get credential parameters for FILES() function based on storage type.

    Returns a dict of FILES() parameters (keys are StarRocks FILES() param names).
    Credentials are read from the storage connection config, never hardcoded.
    """
    if storage_type in ("s3", "minio"):
        access_key, secret_key = resolve_storage_credentials(storage_connection)
        if not access_key or not secret_key:
            return {}
        params = {
            "aws.s3.access_key": access_key,
            "aws.s3.secret_key": secret_key,
        }
        if settings.S3_ENDPOINT:
            params["aws.s3.endpoint"] = settings.S3_ENDPOINT
        return params

    elif storage_type == "azure":
        # Future: Azure Blob credentials
        return {}

    elif storage_type == "gcs":
        # Future: GCS credentials
        return {}

    return {}


def inject_credentials_into_files(
    sql: str, storage_type: str = "s3", storage_connection: str | None = None
) -> str:
    """Inject credential parameters into an existing FILES() call.

    This is a safety net — if the translator didn't include credentials,
    this function ensures they're present.

    Args:
        sql: SQL containing FILES() calls
        storage_type: Storage backend type
        storage_connection: Optional named storage connection to read creds from

    Returns:
        SQL with credentials injected into FILES() calls.
    """
    creds = get_credential_params(storage_type, storage_connection)
    if not creds:
        return sql

    # Build credential string
    cred_parts = [f"'{k}'='{v}'" for k, v in creds.items()]
    cred_str = ", ".join(cred_parts)

    # Check if FILES() already has credentials
    if "aws.s3.access_key" in sql or "azure.account_name" in sql:
        return sql  # Already has credentials

    # Inject credentials into FILES() calls
    # Pattern: FILES('path'='...', 'format'='...')
    import re

    def _inject(match: re.Match) -> str:
        files_content = match.group(1)
        if "access_key" not in files_content:
            files_content = f"{files_content}, {cred_str}"
        return f"FILES({files_content})"

    return re.sub(r'FILES\(([^)]+)\)', _inject, sql)
