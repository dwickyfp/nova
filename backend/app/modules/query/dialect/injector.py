"""Credential injection for FILES() function calls.

Loads storage credentials from config and injects them into SQL
so users never see or type storage credentials.
"""

from app.core.config import settings


def get_credential_params(storage_type: str = "s3") -> dict[str, str]:
    """Get credential parameters for FILES() function based on storage type.

    Returns a dict of FILES() parameters (keys are StarRocks FILES() param names).
    """
    if storage_type == "s3":
        params = {
            "aws.s3.access_key": settings.S3_ACCESS_KEY,
            "aws.s3.secret_key": settings.S3_SECRET_KEY,
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


def inject_credentials_into_files(sql: str, storage_type: str = "s3") -> str:
    """Inject credential parameters into an existing FILES() call.

    This is a safety net — if the translator didn't include credentials,
    this function ensures they're present.

    Args:
        sql: SQL containing FILES() calls
        storage_type: Storage backend type

    Returns:
        SQL with credentials injected into FILES() calls.
    """
    creds = get_credential_params(storage_type)
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
