"""Translate @stage references to StarRocks FILES() function calls.

@stage1.data.csv → FILES('path'='s3://bucket/prefix/data.csv', 'format'='csv', creds...)
"""

import re
from dataclasses import dataclass

from app.common.sql_guard import strip_sql_comments
from app.modules.query.dialect.parser import CommandType, ParsedSQL, StageReference


@dataclass
class StorageConfig:
    """Storage connection config from nova.yaml / stage metadata."""

    storage_type: str  # "s3", "azure", "gcs"
    endpoint: str  # e.g. "http://minio:9000"
    bucket: str  # e.g. "nova-stages"
    base_prefix: str  # e.g. "datalake/bronze/stage1"
    access_key: str = ""
    secret_key: str = ""
    region: str = "us-east-1"
    #: Name of the connection this stage is bound to. Carried so a credential
    #: fallback can resolve the *stage's own* connection rather than the
    #: workspace default (NOVA-68). Empty when unknown; the fallback then
    #: injects nothing rather than guessing a principal.
    storage_connection: str = ""


def build_s3_path(config: StorageConfig, ref: StageReference) -> str:
    """Build the full S3 path for a stage reference.

    @stage1.data.csv with prefix 'datalake/bronze/stage1'
    → s3://bucket/datalake/bronze/stage1/data.csv
    """
    parts = [config.base_prefix.rstrip("/")]

    # Add directory path parts
    if ref.path_parts:
        parts.append("/".join(ref.path_parts))

    # Add file name
    if ref.file_name:
        parts.append(ref.file_name)

    path = "/".join(parts)
    return f"s3://{config.bucket}/{path}"


def build_files_function(
    s3_path: str,
    file_format: str,
    config: StorageConfig,
    credential_params: dict | None = None,
    extra_params: dict[str, str] | None = None,
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
    def assignment(key: str, value: str) -> str:
        quoted_key = key.replace("'", "''")
        quoted_value = value.replace("'", "''")
        return f"'{quoted_key}'='{quoted_value}'"

    params = [assignment("path", s3_path), assignment("format", file_format)]

    # Add credentials
    if credential_params:
        for key, value in credential_params.items():
            params.append(assignment(key, value))
    elif config.access_key:
        params.append(assignment("aws.s3.access_key", config.access_key))
        params.append(assignment("aws.s3.secret_key", config.secret_key))
        if config.endpoint:
            params.append(assignment("aws.s3.endpoint", config.endpoint))
        if config.region:
            params.append(assignment("aws.s3.region", config.region))
        # Required for MinIO / S3-compatible storage
        if config.endpoint and not config.endpoint.startswith("https"):
            params.append("'aws.s3.enable_ssl'='false'")
        params.append("'aws.s3.enable_path_style_access'='true'")
        params.append("'aws.s3.use_aws_sdk_default_behavior'='false'")
        params.append("'aws.s3.use_instance_profile'='false'")

    if extra_params:
        params.extend(assignment(key, value) for key, value in extra_params.items())

    return f"FILES({', '.join(params)})"


def translate_stage_query(
    parsed: ParsedSQL,
    stage_configs: dict[str, StorageConfig],
    format_overrides: dict[str, str] | None = None,
    *,
    files_params: dict[str, str] | None = None,
    files_params_by_ref: dict[int, dict[str, str]] | None = None,
    credential_params_by_stage: dict[str, dict[str, str]] | None = None,
    stage_configs_by_ref: dict[int, StorageConfig] | None = None,
    credential_params_by_ref: dict[int, dict[str, str]] | None = None,
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

    warnings = []
    replacements: list[tuple[int, int, str]] = []

    for index, ref in enumerate(parsed.stage_refs):
        stage_name = ref.stage_name

        config = (stage_configs_by_ref or {}).get(ref.start) or stage_configs.get(stage_name)
        if config is None:
            raise ValueError(f"Stage '{stage_name}' not found")

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
        is_destination = parsed.command_type == CommandType.STAGE_EXPORT and index == 0
        extra_params = (
            {"list_files_only": "true", "list_recursively": "true"}
            if parsed.command_type == CommandType.STAGE_BROWSE
            else None
            if is_destination
            else (files_params_by_ref or {}).get(ref.start, files_params)
            if file_format == "csv"
            else None
        )
        files_func = build_files_function(
            s3_path,
            file_format,
            config,
            credential_params=(credential_params_by_ref or {}).get(ref.start)
            or (credential_params_by_stage or {}).get(stage_name),
            extra_params=extra_params,
        )
        warnings.append(f"Resolved @{stage_name} reference for execution")

        if parsed.original_sql[ref.start : ref.end] != ref.full_match:
            raise ValueError("Stage reference source span does not match the parsed SQL")
        replacements.append((ref.start, ref.end, files_func))

    sql = parsed.original_sql
    for start, end, files_func in sorted(replacements, reverse=True):
        sql = sql[:start] + files_func + sql[end:]

    if parsed.command_type == CommandType.STAGE_BROWSE:
        if len(replacements) != 1 or not re.fullmatch(
            r"\s*LIST(?:\s+FILES)?\s+" + re.escape(parsed.stage_refs[0].full_match) + r"\s*;?\s*",
            strip_sql_comments(parsed.original_sql),
            flags=re.IGNORECASE,
        ):
            raise ValueError("LIST expects exactly one stage reference")
        return f"SELECT * FROM {replacements[0][2]}", warnings

    if parsed.command_type == CommandType.STAGE_LOAD:
        if len(replacements) != 1:
            raise ValueError("COPY INTO table expects exactly one stage source")
        match = re.fullmatch(
            r"\s*COPY\s+INTO\s+(?P<table>(?:`[^`]+`|[\w$]+)(?:\.(?:`[^`]+`|[\w$]+))?)\s+FROM\s+"
            + re.escape(parsed.stage_refs[0].full_match)
            + r"\s*;?\s*",
            strip_sql_comments(parsed.original_sql),
            flags=re.IGNORECASE,
        )
        if not match:
            raise ValueError("Unsupported COPY INTO load syntax")
        return f"INSERT INTO {match.group('table')} SELECT * FROM {replacements[0][2]}", warnings

    if parsed.command_type == CommandType.STAGE_EXPORT:
        target = parsed.stage_refs[0]
        prefix = strip_sql_comments(parsed.original_sql[: target.start])
        suffix = strip_sql_comments(sql.split(replacements[0][2], 1)[1]).strip().rstrip(";").strip()
        if re.fullmatch(r"\s*COPY\s+INTO\s+", prefix, flags=re.IGNORECASE):
            match = re.fullmatch(r"FROM\s+(.+)", suffix, flags=re.IGNORECASE | re.DOTALL)
            if not match:
                raise ValueError("COPY INTO stage requires a source")
            source = match.group(1).strip()
            if re.fullmatch(r"(?:`[^`]+`|[\w$]+)(?:\.(?:`[^`]+`|[\w$]+))?", source):
                suffix = f"SELECT * FROM {source}"
            elif re.match(r"(?is)^(SELECT|WITH)\s+", source):
                suffix = source
            else:
                raise ValueError("Unsupported COPY INTO export source")
        elif not re.fullmatch(r"\s*INSERT\s+INTO\s+", prefix, flags=re.IGNORECASE):
            raise ValueError("Unsupported stage export syntax")
        elif not re.match(r"(?is)^(SELECT|WITH|VALUES)\s+", suffix):
            raise ValueError("INSERT INTO stage requires a query or VALUES")
        return f"INSERT INTO {replacements[0][2]} {suffix}", warnings

    return sql, warnings


def detect_format_from_filename(filename: str) -> str:
    """Detect file format from filename extension.

    Args:
        filename: e.g. "data.csv", "events.parquet.gz"

    Returns:
        Format string: csv, parquet, json, orc, etc.
    """
    # Handle compound extensions like .csv.gz, .parquet.snappy
    parts = filename.lower().split(".")

    if len(parts) >= 3:
        # Check for compression extensions
        compression = parts[-1]
        if compression in ("gz", "bz2", "snappy", "zstd", "lzo"):
            return parts[-2]  # Return the actual format, not compression

    if len(parts) >= 2:
        ext = parts[-1]
        format_map = {
            "csv": "csv",
            "tsv": "csv",
            "json": "json",
            "jsonl": "json",
            "ndjson": "json",
            "parquet": "parquet",
            "orc": "orc",
            "avro": "avro",
            "txt": "csv",
            "xml": "json",
            "xlsx": "csv",
            "xls": "csv",
            "log": "csv",
            "sql": "csv",
        }
        if ext in format_map:
            return format_map[ext]
        # Check second-to-last for compound extensions
        if len(parts) >= 3 and parts[-2] in ("csv", "tsv", "json", "parquet", "orc"):
            return parts[-2]

    return "csv"  # Default fallback
