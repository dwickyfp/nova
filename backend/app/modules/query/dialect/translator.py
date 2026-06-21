"""Translate @stage references to StarRocks FILES() function calls.

@stage1.data.csv → FILES('path'='s3://bucket/prefix/data.csv', 'format'='csv', creds...)
"""

import re
from dataclasses import dataclass

from app.modules.query.dialect.parser import ParsedSQL, StageReference


@dataclass
class StorageConfig:
    """Storage connection config from nova.yaml / stage metadata."""
    storage_type: str       # "s3", "azure", "gcs"
    endpoint: str           # e.g. "http://minio:9000"
    bucket: str             # e.g. "nova-stages"
    base_prefix: str        # e.g. "datalake/bronze/stage1"
    access_key: str = ""
    secret_key: str = ""
    region: str = "us-east-1"


def build_s3_path(config: StorageConfig, ref: StageReference) -> str:
    """Build the full S3 path for a stage reference.

    @stage1.data.csv with prefix 'datalake/bronze/stage1'
    → s3://bucket/datalake/bronze/stage1/data.csv
    """
    parts = [config.base_prefix.rstrip('/')]

    # Add directory path parts
    if ref.path_parts:
        parts.append('/'.join(ref.path_parts))

    # Add file name
    if ref.file_name:
        parts.append(ref.file_name)

    path = '/'.join(parts)
    return f"s3://{config.bucket}/{path}"


def build_files_function(
    s3_path: str,
    file_format: str,
    config: StorageConfig,
    credential_params: dict | None = None,
) -> str:
    """Build a StarRocks FILES() function call.

    Args:
        s3_path: Full S3 path (s3://bucket/prefix/file.csv)
        file_format: File format (csv, parquet, json, etc.)
        config: Storage config for credential injection
        credential_params: Pre-computed credential params (if available)

    Returns:
        FILES('path'='...', 'format'='...', 'aws.s3.access_key'='...', ...)
    """
    params = [
        f"'path'='{s3_path}'",
        f"'format'='{file_format}'",
    ]

    # Add credentials
    if credential_params:
        for key, value in credential_params.items():
            params.append(f"'{key}'='{value}'")
    elif config.access_key:
        params.append(f"'aws.s3.access_key'='{config.access_key}'")
        params.append(f"'aws.s3.secret_key'='{config.secret_key}'")
        if config.endpoint:
            params.append(f"'aws.s3.endpoint'='{config.endpoint}'")
        if config.region:
            params.append(f"'aws.s3.region'='{config.region}'")
        # Required for MinIO / S3-compatible storage
        if config.endpoint and not config.endpoint.startswith("https"):
            params.append("'aws.s3.enable_ssl'='false'")
        params.append("'aws.s3.enable_path_style_access'='true'")
        params.append("'aws.s3.use_aws_sdk_default_behavior'='false'")
        params.append("'aws.s3.use_instance_profile'='false'")

    return f"FILES({', '.join(params)})"


def translate_stage_query(
    parsed: ParsedSQL,
    stage_configs: dict[str, StorageConfig],
    format_overrides: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Translate @stage references in SQL to FILES() calls.

    Args:
        parsed: Parsed SQL with @stage references
        stage_configs: Map of stage_name → StorageConfig
        format_overrides: Map of stage_name → forced format (skip auto-detect)

    Returns:
        (translated_sql, warnings)

    Raises:
        ValueError: If a referenced stage doesn't exist in configs.
    """
    if not parsed.stage_refs:
        return parsed.original_sql, []

    sql = parsed.original_sql
    warnings = []

    for ref in parsed.stage_refs:
        stage_name = ref.stage_name

        if stage_name not in stage_configs:
            raise ValueError(f"Stage '{stage_name}' not found")

        config = stage_configs[stage_name]

        # Build S3 path
        s3_path = build_s3_path(config, ref)

        # Determine format
        if format_overrides and stage_name in format_overrides:
            file_format = format_overrides[stage_name]
        elif ref.file_name:
            file_format = detect_format_from_filename(ref.file_name)
        else:
            file_format = "csv"  # default
            warnings.append(f"⚠️ No file extension for @{stage_name}, defaulting to CSV")

        # Build FILES() function
        files_func = build_files_function(s3_path, file_format, config)
        warnings.append(f"Resolved @{stage_name} reference for execution")

        # Replace @stage reference with FILES() call
        # Handle both standalone and boundary cases
        pattern = re.escape(ref.full_match)
        sql = re.sub(pattern, files_func, sql, count=1)

    return sql, warnings


def detect_format_from_filename(filename: str) -> str:
    """Detect file format from filename extension.

    Args:
        filename: e.g. "data.csv", "events.parquet.gz"

    Returns:
        Format string: csv, parquet, json, orc, etc.
    """
    # Handle compound extensions like .csv.gz, .parquet.snappy
    parts = filename.lower().split('.')

    if len(parts) >= 3:
        # Check for compression extensions
        compression = parts[-1]
        if compression in ('gz', 'bz2', 'snappy', 'zstd', 'lzo'):
            return parts[-2]  # Return the actual format, not compression

    if len(parts) >= 2:
        ext = parts[-1]
        format_map = {
            'csv': 'csv', 'tsv': 'csv', 'json': 'json', 'jsonl': 'json',
            'ndjson': 'json', 'parquet': 'parquet', 'orc': 'orc',
            'avro': 'avro', 'txt': 'csv', 'xml': 'json',
            'xlsx': 'csv', 'xls': 'csv', 'log': 'csv', 'sql': 'csv',
        }
        if ext in format_map:
            return format_map[ext]
        # Check second-to-last for compound extensions
        if len(parts) >= 3 and parts[-2] in ('csv', 'tsv', 'json', 'parquet', 'orc'):
            return parts[-2]

    return "csv"  # Default fallback
