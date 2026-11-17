"""A pipe's stage: authorize, then resolve storage, list files and build their reads.

The order is the stage contract's: the stage scope is authorized for the acting
principal and role (Ranger) before its storage connection or secret is touched.
The resolved credentials live only in this run's memory and in the statement
sent to the engine; they are never stored, logged or returned.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.modules.pipes.definition import PipeDefinitionError, PipeSource, glob_regex, list_prefix

#: A listing larger than this is refused rather than staged partly.
MAX_LISTED_FILES = 100000


@dataclass(frozen=True, slots=True)
class StageLocation:
    """One authorized stage registration and the folder segments under it."""

    stage: dict[str, Any]
    segments: tuple[str, ...]

    @property
    def label(self) -> str:
        return f"@{self.stage['database_name']}.{self.stage['schema_name']}.{self.stage['name']}"


def _actor(username: str, role: str):
    from app.modules.stages.service import caller_actor

    return caller_actor(username, role)


async def resolve_stage(
    username: str, role: str, source: PipeSource, database: str | None, schema: str | None,
) -> StageLocation:
    """Find the one stage the path names and authorize reading it, as ``username``.

    The same lookup as ``@stage`` in a query: ``@stage``, ``@schema.stage`` or
    ``@database.schema.stage``. Raises ``StageAccessDenied`` when not allowed.
    """
    from app.modules.stages.service import stage_metadata

    actor = _actor(username, role)
    parts = list(source.path)
    async with stage_metadata(actor) as registrations:
        candidates = [(database, schema, source.stage_name, 0)]
        if parts:
            candidates.append((database, source.stage_name, parts[0], 1))
        if len(parts) > 1:
            candidates.append((source.stage_name, parts[0], parts[1], 2))
        matches: list[tuple[dict[str, Any], int]] = []
        for db_name, schema_name, name, consumed in candidates:
            found = await registrations.identities(
                name=name, database=db_name, schema=schema_name,
            )
            if found:
                matches = [(row, consumed) for row in found]
        if len(matches) != 1:
            where = f" in {database}" if database else ""
            raise PipeDefinitionError(
                f"Stage '{source.stage_name}' "
                + ("matches more than one stage" if matches else "not found") + where
            )
        identity, consumed = matches[0]
        row = await registrations.get_stage(identity["id"], action="read")
    if row is None:
        raise PipeDefinitionError(f"Stage '{source.stage_name}' not found")
    return StageLocation(row, (*parts[consumed:], source.pattern))


async def authorize_stage(username: str, role: str, stage_id: str) -> dict[str, Any] | None:
    """Re-authorize reading a known stage as ``username``; ``None`` when it is gone."""
    from app.modules.stages.service import stage_metadata

    async with stage_metadata(_actor(username, role)) as registrations:
        return await registrations.get_stage(stage_id, action="read")


def storage_config(stage: dict[str, Any]) -> Any:
    """The stage's storage location with its credentials, for this run only."""
    from app.core.config import require_storage_connection, to_docker_endpoint
    from app.modules.query.dialect.injector import resolve_storage_credentials
    from app.modules.query.dialect.translator import StorageConfig

    name = stage["storage_connection"]
    connection = require_storage_connection(name)
    if (connection.type or "s3").lower() not in {"s3", "minio"}:
        raise PipeDefinitionError("Pipes read stages on S3-compatible storage")
    access_key, secret_key = resolve_storage_credentials(name)
    prefix = (stage.get("base_prefix") or "").strip("/") or (
        f"{stage['database_name']}/{stage['schema_name']}/{stage['name']}"
    )
    return StorageConfig(
        storage_type=connection.type, endpoint=to_docker_endpoint(connection.endpoint),
        bucket=connection.bucket, base_prefix=prefix, access_key=access_key,
        secret_key=secret_key, region=connection.region or "us-east-1",
        storage_connection=name,
    )


async def list_files(
    config: Any, segments: tuple[str, ...],
) -> list[tuple[str, str, int, datetime]]:
    """Every object under the stage matching the glob: (relative path, etag, size, modified)."""
    import boto3  # type: ignore[import-untyped]
    from botocore.config import Config as BotoConfig  # type: ignore[import-untyped]

    from app.core.config import require_storage_connection

    pattern = glob_regex(segments)
    prefix = f"{config.base_prefix}/"
    listing = prefix + list_prefix(segments)

    def scan() -> list[tuple[str, str, int, datetime]]:
        client = boto3.client(
            "s3",
            endpoint_url=require_storage_connection(config.storage_connection).endpoint,
            aws_access_key_id=config.access_key or None,
            aws_secret_access_key=config.secret_key or None,
            config=BotoConfig(signature_version="s3v4"),
            region_name=config.region or "us-east-1",
        )
        found = []
        pages = client.get_paginator("list_objects_v2").paginate(
            Bucket=config.bucket, Prefix=listing,
        )
        for page in pages:
            for item in page.get("Contents", []):
                relative = item["Key"][len(prefix):]
                if not relative or relative.endswith("/") or not pattern.fullmatch(relative):
                    continue
                found.append((relative, str(item.get("ETag") or "").strip('"') or
                              f"size-{item['Size']}", int(item["Size"]), item["LastModified"]))
                if len(found) > MAX_LISTED_FILES:
                    raise PipeDefinitionError(
                        f"The pipe's path matches more than {MAX_LISTED_FILES} files; "
                        "narrow the folder or pattern"
                    )
        return found

    return await asyncio.to_thread(scan)


async def csv_layout(config: Any, relative: str) -> tuple[dict[str, str], list[str] | None]:
    """Delimiter, enclosure and header columns of a CSV, as the worksheet detects them."""
    from app.modules.query.dialect.parser import CommandType, ParsedSQL
    from app.sql_frontend.execution.stages import StageRuntime
    from app.sql_frontend.stage_parser import StageReference

    *folders, file_name = relative.split("/")
    reference = StageReference(
        full_match="", stage_name="__pipe", path_parts=folders, file_name=file_name,
        is_directory=False, original_text="", start=0, end=0,
    )
    return await StageRuntime()._detect_csv_params(
        ParsedSQL(CommandType.STAGE_QUERY, [reference], "", "", []), {"__pipe": config},
    )


def file_read(
    config: Any, relative: str, source: PipeSource, index: int, *,
    csv_params: dict[str, str] | None = None, columns: list[str] | None = None,
) -> str:
    """One file's relation SQL: ``FILES(...)`` with the stage's location and credentials."""
    from app.modules.query.dialect.translator import build_files_function

    options = dict(source.options)
    if source.form == "stage" and source.format == "csv":
        options = {**(csv_params or {}), **options}
    path = f"s3://{config.bucket}/{config.base_prefix}/{relative}"
    read = build_files_function(path, source.format, config, extra_params=options or None)
    if columns and source.form == "stage" and source.format == "csv":
        projection = ", ".join(
            f"`${position}` AS `{name.replace('`', '``')}`"
            for position, name in enumerate(columns, 1)
        )
        return f"(SELECT {projection} FROM {read}) AS `__nova_file_{index}`"
    return read
