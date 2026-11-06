"""Iceberg fixtures for the catalog suites, created the way a DBA would.

Nova creates catalogs only from a connection (StarRocks, PostgreSQL,
Snowflake); Iceberg and Hive catalogs made outside Nova are still synced,
listed, altered, and dropped. The suites make one with raw SQL as root, on the
test stack's MinIO (a ``hadoop`` catalog: the test stack has no metastore).
"""

from __future__ import annotations

from app.core.config import settings, to_docker_endpoint
from tests.conftest import s3_credentials


def iceberg_catalog_sql(name: str, warehouse_uri: str, comment: str | None = None) -> str:
    """``CREATE EXTERNAL CATALOG`` for a hadoop Iceberg catalog on the stack's MinIO."""
    access_key, secret_key = s3_credentials()
    endpoint = to_docker_endpoint(settings.S3_ENDPOINT)
    params = {
        "type": "iceberg",
        "iceberg.catalog.type": "hadoop",
        "iceberg.catalog.warehouse": warehouse_uri,
        "aws.s3.access_key": access_key,
        "aws.s3.secret_key": secret_key,
        "aws.s3.endpoint": endpoint,
        "aws.s3.enable_path_style_access": "true",
        "aws.s3.enable_ssl": "false",
        "aws.s3.region": "us-east-1",
    }
    properties = ", ".join(f"'{key}' = '{value}'" for key, value in params.items())
    clause = f"COMMENT '{comment}' " if comment else ""
    return f"CREATE EXTERNAL CATALOG {name} {clause}PROPERTIES ({properties})"
