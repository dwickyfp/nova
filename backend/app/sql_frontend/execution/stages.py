from __future__ import annotations

import asyncio
import fnmatch
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

import asyncmy  # type: ignore[import-untyped]

from app.core.config import get_storage_connection, to_docker_endpoint
from app.core.database import db
from app.modules.query.dialect.injector import resolve_storage_credentials
from app.modules.query.dialect.parser import CommandType, ParsedSQL
from app.modules.query.dialect.translator import StorageConfig
from app.modules.stages.access import check_stage_access

logger = logging.getLogger(__name__)


class StageRuntime:
    def __init__(
        self,
        *,
        access_check: Callable[..., Awaitable[Any]] = check_stage_access,
        storage_connection: Callable[..., Any] = get_storage_connection,
        credentials: Callable[..., tuple[str, str]] = resolve_storage_credentials,
    ) -> None:
        self._check_stage_access = access_check
        self._storage_connection = storage_connection
        self._resolve_credentials = credentials

    async def _resolve_stage_refs(
        self,
        parsed: ParsedSQL,
        *,
        database: str | None,
        schema: str | None,
        username: str,
        password: str,
        role: str | None,
        connection: asyncmy.Connection | None = None,
    ) -> tuple[ParsedSQL, dict[int, StorageConfig]]:
        """Bind each reference to one authorized metadata row before resolving secrets."""
        result = await db.execute_system(
            "SELECT name, database_name, schema_name, storage_connection, base_prefix "
            "FROM NOVA_SYSTEM.CONFIG_STAGES"
        )
        rows = [
            {
                "name": row[0],
                "database_name": row[1],
                "schema_name": row[2],
                "storage_connection": row[3],
                "base_prefix": row[4],
            }
            for row in result["rows"]
        ]
        selected = []
        for index, ref in enumerate(parsed.stage_refs):
            candidates = [(database, schema, ref.stage_name, 0)]
            if ref.path_parts:
                candidates.append((database, ref.stage_name, ref.path_parts[0], 1))
            if len(ref.path_parts) > 1:
                candidates.append((ref.stage_name, ref.path_parts[0], ref.path_parts[1], 2))
            matches = []
            for db_name, schema_name, name, consumed in candidates:
                found = [
                    row
                    for row in rows
                    if row["name"] == name
                    and (db_name is None or row["database_name"] == db_name)
                    and (schema_name is None or row["schema_name"] == schema_name)
                ]
                if found:
                    matches = [(row, consumed) for row in found]
            if len(matches) != 1:
                raise ValueError(
                    f"Stage reference {ref.full_match!r} is "
                    + ("ambiguous" if matches else "not found")
                )
            row, consumed = matches[0]
            action = (
                "write"
                if parsed.command_type == CommandType.STAGE_EXPORT and index == 0
                else "read"
            )
            action = ref.access or action
            if action not in {"read", "write"}:
                raise ValueError("Invalid stage access direction")
            selected.append((ref, row, consumed, action))

        for _, row, _, action in selected:
            await self._check_stage_access(
                row,
                action=action,
                username=username,
                password=password,
                active_role=role,
                connection=connection,
            )

        configs: dict[int, StorageConfig] = {}
        refs = []
        for ref, row, consumed, _ in selected:
            storage_conn = row["storage_connection"]
            conn = self._storage_connection(storage_conn)
            access_key, secret_key = self._resolve_credentials(storage_conn)
            prefix = (row["base_prefix"] or "").strip("/")
            if not prefix:
                prefix = f"{row['database_name']}/{row['schema_name']}/{row['name']}"
            refs.append(replace(ref, stage_name=row["name"], path_parts=ref.path_parts[consumed:]))
            configs[ref.start] = StorageConfig(
                storage_type=conn.type,
                endpoint=to_docker_endpoint(conn.endpoint),
                bucket=conn.bucket,
                base_prefix=prefix,
                access_key=access_key,
                secret_key=secret_key,
                region=conn.region or "us-east-1",
                storage_connection=storage_conn,
            )
        return replace(parsed, stage_refs=refs), configs

    async def _detect_csv_params(
        self,
        parsed: ParsedSQL,
        stage_configs: dict[str, StorageConfig],
    ) -> tuple[dict[str, str], list[str] | None]:
        """Pre-read CSV file from MinIO to detect delimiter and header.

        Returns (params_dict, column_names_or_None).
        params_dict: FILES() params like {"csv.column_separator": ",", "csv.skip_header": "1"}
        column_names: list of header column names if detected, else None
        """
        if not parsed.stage_refs:
            logger.warning("CSV detect: no stage references in the parsed statement")
            return {}, None

        ref = parsed.stage_refs[0]
        # Detect format from file extension
        ext = ""
        if ref.file_name and "." in ref.file_name:
            ext = ref.file_name.rsplit(".", 1)[-1].lower()
        if ext not in ("csv", "tsv"):
            logger.warning("CSV detect: %r is not csv/tsv (ext=%r)", ref.file_name, ext)
            return {}, None

        config = stage_configs.get(ref.stage_name)
        if not config:
            # The stage exists as a row but could not be resolved into a
            # StorageConfig, so the FILES() call gets no delimiter/header
            # tuning and the caller sees untuned rows rather than an error.
            logger.warning(
                "CSV detect: stage %r is not in the resolved stage configs %r",
                ref.stage_name,
                sorted(stage_configs),
            )
            return {}, None

        try:
            import boto3  # type: ignore[import-untyped]
            from botocore.config import Config as BotoConfig  # type: ignore[import-untyped]

            # Build S3 key
            parts = ref.path_parts + [ref.file_name] if ref.file_name else ref.path_parts
            s3_key = "/".join([config.base_prefix] + parts)

            def read_header() -> str:
                s3 = boto3.client(
                    "s3",
                    endpoint_url=(
                        self._storage_connection(config.storage_connection).endpoint
                        if config.storage_connection
                        else config.endpoint
                    ),
                    aws_access_key_id=config.access_key,
                    aws_secret_access_key=config.secret_key,
                    config=BotoConfig(signature_version="s3v4"),
                    region_name=config.region or "us-east-1",
                )
                selected_key = s3_key
                wildcard_positions = [s3_key.index(char) for char in "*?[" if char in s3_key]
                if wildcard_positions:
                    prefix = s3_key[:min(wildcard_positions)]
                    pages = s3.get_paginator("list_objects_v2").paginate(
                        Bucket=config.bucket, Prefix=prefix
                    )
                    # A default is required: StopIteration raised in a worker
                    # thread cannot cross into the awaiting future, which then
                    # never completes and the statement hangs.
                    selected_key = next(
                        (
                            item["Key"]
                            for page in pages
                            for item in page.get("Contents", [])
                            if fnmatch.fnmatchcase(item["Key"], s3_key)
                        ),
                        None,
                    )
                    if selected_key is None:
                        # Nothing to sample; the engine reports the empty match.
                        return ""
                resp = s3.get_object(Bucket=config.bucket, Key=selected_key, Range="bytes=0-8191")
                body = resp["Body"]
                try:
                    return body.read(8192).decode("utf-8", errors="replace")
                finally:
                    body.close()

            raw = await asyncio.to_thread(read_header)
            lines = raw.split("\n")
            if len(lines) < 2:
                logger.warning(
                    "CSV detect: object %r/%r returned %d line(s); cannot detect",
                    config.bucket,
                    s3_key,
                    len(lines),
                )
                return {}, None

            first_line = lines[0].strip()

            # Detect delimiter by counting occurrences in first line
            candidates = [
                (",", first_line.count(",")),
                (";", first_line.count(";")),
                ("\t", first_line.count("\t")),
                ("|", first_line.count("|")),
            ]
            # Pick the delimiter with highest count (must be > 0)
            best_delim, best_count = max(candidates, key=lambda x: x[1])
            if best_count == 0:
                best_delim = ","

            # Detect enclosure
            enclose = ""
            if first_line.startswith('"') and first_line.endswith('"'):
                enclose = '"'

            # Detect if first line is a header:
            # Headers typically contain text, not numbers
            second_line = lines[1].strip() if len(lines) > 1 else ""
            first_fields = first_line.split(best_delim)
            second_fields = second_line.split(best_delim)

            is_header = False
            column_names = None
            if first_fields and second_fields and len(first_fields) == len(second_fields):
                # Check if first row looks like text (header) and second like data
                text_count = sum(
                    1
                    for f in first_fields
                    if not f.strip().replace("-", "").replace(".", "").isdigit()
                )
                is_header = text_count > len(first_fields) / 2
                if is_header:
                    # Extract clean column names from header
                    column_names = [f.strip().strip('"').strip("'") for f in first_fields]

            params: dict[str, str] = {
                "csv.column_separator": best_delim,
                "csv.trim_space": "true",
            }
            if enclose:
                params["csv.enclose"] = enclose
                params["csv.escape"] = "\\\\"
            if is_header:
                params["csv.skip_header"] = "1"

            return params, column_names

        except Exception:
            # Falling back to defaults is correct — a CSV that cannot be
            # pre-read still loads, without delimiter or header tuning. What is
            # NOT correct is doing it silently: a boto3 failure here (unreachable
            # endpoint, missing credential) makes the query return typed rows
            # where the caller expected a header, which surfaces as a confusing
            # shape assertion far from the cause. Name it in the log instead.
            logger.warning("CSV parameter detection failed for stage %r", ref.stage_name)
            return {}, None
