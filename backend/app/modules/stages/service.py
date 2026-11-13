"""Stage Manager service — registrations in PostgreSQL, files via MinIO/S3.

Stage registrations live in ``nova_meta.config_stages`` and are read and
changed through :class:`~app.modules.stages.metadata_service.StageMetadataService`
in the caller's actor-bound metadata transaction. Ranger authorizes a stage's
schema before its storage connection or prefix is read; files are listed,
uploaded, downloaded and deleted through the boto3 S3 client afterwards,
outside the metadata transaction.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, BinaryIO

import boto3
from botocore.client import Config as BotoConfig

from app.core.config import require_storage_connection, settings
from app.core.metadata_scope import acting_actor, metadata_transaction
from app.modules.access_control.security_context import SecurityContext, SecurityContextError
from app.modules.query.dialect.injector import resolve_storage_credentials

#: Fields of a registration the API and file operations use.
STAGE_FIELDS = (
    "id", "name", "database_name", "schema_name", "storage_connection",
    "base_prefix", "created_at", "created_by",
)


class StageRegistrationError(ValueError):
    pass


class StagePathError(ValueError):
    """A user-supplied stage path is malformed or escapes the stage prefix.

    Subclasses ``ValueError`` so existing ``except ValueError`` callers keep
    working, while giving the router a distinct type to map to 400 rather than
    the 404 used for a missing stage/file (NOVA-106).
    """


def public_stage(row: dict[str, Any]) -> dict[str, Any]:
    """A registration without its revision, tombstone or namespace bookkeeping."""
    return {name: row.get(name) for name in STAGE_FIELDS}


def caller_actor(
    username: str,
    role: str | None,
    *,
    session_id: str | None = None,
    version: int | None = None,
) -> SecurityContext:
    """The metadata actor of a caller known only by name and active role."""
    from app.modules.stages.access import StageAccessDenied

    if not role:
        raise StageAccessDenied("Stage access requires an active role")
    bound = acting_actor()
    if bound is not None:
        if (
            bound.principal != username or bound.active_role != role
            or (session_id is not None and bound.session_id != session_id)
            or (version is not None and bound.security_context_version != version)
        ):
            raise StageAccessDenied("Stage access does not match the active security context")
        return bound
    try:
        return SecurityContext(
            principal=username,
            active_role=role,
            session_id=session_id,
            security_context_version=version or 1,
        )
    except SecurityContextError as exc:
        raise StageAccessDenied("Stage access requires a valid security context") from exc


@asynccontextmanager
async def stage_metadata(
    actor: SecurityContext,
    *,
    read_only: bool = True,
    access_check: Callable[..., Awaitable[Any]] | None = None,
) -> AsyncIterator[Any]:
    """Join only the same complete actor in this task's metadata transaction."""
    from app.modules.stages.metadata_service import StageMetadataService

    async with metadata_transaction(actor, read_only=read_only) as opened:
        yield StageMetadataService(opened.connection, actor, access_check=access_check)


async def discover_stages(
    actor: SecurityContext, *, database: str | None = None, schema: str | None = None,
    name: str | None = None,
) -> list[dict[str, Any]]:
    """Stage identities (id, name, database, schema) for authorization.

    Returns no storage connection or prefix: the caller authorizes a scope
    before it reads a registration's payload.
    """
    async with stage_metadata(actor) as stages:
        return await stages.identities(name=name, database=database, schema=schema)


class StageService:
    """Business logic for stage management and file operations."""

    # ── Stage registrations ─────────────────────────────────────

    async def list_stages(
        self, actor: SecurityContext, *, database: str | None = None,
    ) -> list[dict]:
        """Registrations in every schema the actor may read."""
        async with stage_metadata(actor) as stages:
            rows = await stages.list_stages(database=database)
        return [public_stage(row) for row in rows]

    async def get_stage(
        self, stage_id: str, actor: SecurityContext, *, action: str = "read",
    ) -> dict | None:
        """One registration after Ranger allows ``action`` on its schema.

        ``None`` when it does not exist; ``StageAccessDenied`` when it is not allowed.
        """
        async with stage_metadata(actor) as stages:
            row = await stages.get_stage(stage_id, action=action)  # type: ignore[arg-type]
        return public_stage(row) if row else None

    async def create_stage(self, data: dict, actor: SecurityContext) -> dict:
        """Register a stage; the success audit commits with the registration."""
        from app.modules.stages.schemas import StageCreate

        body = StageCreate.model_validate(data)
        async with stage_metadata(actor, read_only=False) as stages:
            return public_stage(await stages.create_stage(body))

    async def delete_stage(self, stage_id: str, actor: SecurityContext) -> bool:
        """Tombstone a registration. ``False`` when it does not exist.

        The revision read here is compared again by the update, so of two
        concurrent deletions one succeeds and the other reports a conflict.
        Files under the stage's prefix are not deleted.
        """
        async with stage_metadata(actor, read_only=False) as stages:
            row = await stages.get_stage(stage_id, action="delete")
            if row is None:
                return False
            await stages.delete_stage(stage_id, expected_revision=row["metadata_revision"])
        return True

    # ── S3 / MinIO client ──────────────────────────────────────

    @staticmethod
    def _s3_client(storage_connection: str):
        """Create a boto3 S3 client pointed at the stage's storage connection.

        Credentials, endpoint and bucket all come from the named connection in
        `nova.yaml`. If the connection has no credentials configured the client
        is still built (boto3 supports anonymous/instance-profile auth) rather
        than silently falling back to a hardcoded default.

        ``storage_connection`` is required for correctness, not cosmetics: the
        connection may carry its own ``secret_ref``, and defaulting here would
        read and write a non-default stage's objects with the workspace-default
        principal (NOVA-67). A resolution failure raises rather than falling
        back.
        """
        conn = require_storage_connection(storage_connection)
        access_key, secret_key = resolve_storage_credentials(storage_connection)
        return boto3.client(
            "s3",
            endpoint_url=conn.endpoint or settings.S3_ENDPOINT,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            config=BotoConfig(signature_version="s3v4"),
            region_name=conn.region or "us-east-1",
        )

    @staticmethod
    def _s3_client_for_stage(stage: dict):
        """Return ``(client, bucket)`` for the stage's own storage connection.

        Every file operation goes through here so they all use the same
        principal and bucket the stage is actually registered against. Reading
        ``settings.S3_BUCKET`` / the default credentials instead made the file
        browser disagree with ``@stage`` queries for any non-default stage
        (NOVA-67).
        """
        connection = stage["storage_connection"]
        conn = require_storage_connection(connection)
        return StageService._s3_client(connection), (conn.bucket or settings.S3_BUCKET)

    @staticmethod
    def _resolve_prefix(stage: dict) -> str:
        """Build the S3 key prefix for a stage.

        Format: <base_prefix> (stripped of leading/trailing slashes).
        Falls back to <database_name>/<schema_name>/<name> when base_prefix is empty.
        """
        base = (stage.get("base_prefix") or "").strip("/")
        if not base:
            base = f"{stage['database_name']}/{stage['schema_name']}/{stage['name']}"
        return base

    @staticmethod
    def _safe_relative_path(relative: str, *, field: str = "filename") -> str:
        """Validate and normalize a user-supplied path inside a stage.

        ``filename:path`` / ``prefix`` arrive straight from the URL and are
        concatenated into the S3 key. Without normalization ``../`` escapes the
        stage prefix and reaches every other stage in the same bucket
        (NOVA-106), and a backslash or absolute segment would do the same on an
        S3 client that treats it as a separator. Reject before the key is built,
        rather than after, so there is never a partially-formed key to leak via
        an error path.

        Returns the normalized relative path (no leading/trailing slash).
        Raises ``ValueError`` naming the offending *field* (never leaking more
        of the input than necessary) when the path is unsafe.
        """
        if relative is None:
            raise StagePathError(f"Invalid {field}: path is required")
        if "\x00" in relative:
            raise StagePathError(f"Invalid {field}: NUL byte is not allowed")
        # S3 keys are '/'-separated. A backslash would be a literal key byte, but
        # some clients/OSes treat it as a separator; refuse it so the boundary
        # does not depend on which one is interpreting the value.
        if "\\" in relative:
            raise StagePathError(f"Invalid {field}: backslash is not allowed")
        if relative.startswith("/"):
            raise StagePathError(f"Invalid {field}: absolute paths are not allowed")

        parts: list[str] = []
        for raw_segment in relative.split("/"):
            if raw_segment in ("", "."):
                continue
            if raw_segment == "..":
                raise StagePathError(f"Invalid {field}: '..' segments are not allowed")
            parts.append(raw_segment)

        if not parts:
            raise StagePathError(f"Invalid {field}: path resolves to the stage root")
        return "/".join(parts)

    @classmethod
    def _build_key(cls, stage: dict, filename: str, *, field: str = "filename") -> str:
        """Build the full S3 key for a file inside a stage, safely.

        The relative path is validated/normalized first and then the resolved
        key is prefix-checked as defence in depth, so an escape that slipped
        past normalization still cannot leave the stage.
        """
        prefix = cls._resolve_prefix(stage)
        relative = cls._safe_relative_path(filename, field=field)
        key = f"{prefix}/{relative}"
        if key != prefix and not key.startswith(f"{prefix}/"):
            raise StagePathError(f"Invalid {field}: path escapes the stage prefix")
        return key

    # ── File operations ─────────────────────────────────────────

    # File operations take a registration the caller has already authorized
    # for the action through ``get_stage``.

    async def list_files(self, stage: dict, prefix: str = "") -> list[dict]:
        """List files under the stage's S3 path (non-recursive, delimited '/')."""

        # Trailing slash is required for the delimiter to work correctly.
        base_prefix = self._resolve_prefix(stage)
        if prefix:
            safe_prefix = self._safe_relative_path(prefix, field="prefix")
            s3_prefix = f"{base_prefix}/{safe_prefix}/"
        else:
            s3_prefix = f"{base_prefix}/"

        s3, bucket = self._s3_client_for_stage(stage)

        def scan() -> list[dict]:
            paginator = s3.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=bucket, Prefix=s3_prefix, Delimiter="/")
            files: list[dict] = []
            base_len = len(s3_prefix)
            for page in pages:
                for cp in page.get("CommonPrefixes", []):
                    name = cp["Prefix"][base_len:].rstrip("/")
                    files.append(
                        {
                            "name": name,
                            "size": 0,
                            "last_modified": None,
                            "is_dir": True,
                        }
                    )
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key == s3_prefix or not key[base_len:]:
                        continue
                    files.append(
                        {
                            "name": key[base_len:],
                            "size": obj["Size"],
                            "last_modified": obj["LastModified"].isoformat(),
                            "is_dir": False,
                        }
                    )
            return files

        return await asyncio.to_thread(scan)

    async def upload_file(self, stage: dict, filename: str, content: bytes) -> dict:
        """Upload a file to the stage's S3 path."""
        s3_key = self._build_key(stage, filename)
        s3, bucket = self._s3_client_for_stage(stage)
        await asyncio.to_thread(s3.put_object, Bucket=bucket, Key=s3_key, Body=content)

        return {"filename": filename, "size": len(content)}

    async def upload_stream(self, stage: dict, filename: str, stream: BinaryIO) -> dict:
        s3_key = self._build_key(stage, filename)
        s3, bucket = self._s3_client_for_stage(stage)

        def upload() -> int:
            position = stream.tell()
            stream.seek(0, os.SEEK_END)
            size = stream.tell() - position
            stream.seek(position)
            s3.upload_fileobj(stream, bucket, s3_key)
            return size

        size = await asyncio.to_thread(upload)
        return {"filename": filename, "size": size}

    async def open_file(self, stage: dict, filename: str) -> Any:
        s3_key = self._build_key(stage, filename)
        s3, bucket = self._s3_client_for_stage(stage)
        response = await asyncio.to_thread(s3.get_object, Bucket=bucket, Key=s3_key)
        return response["Body"]

    async def download_file(self, stage: dict, filename: str) -> bytes:
        """Download a file from the stage's S3 path. Returns raw bytes."""
        s3_key = self._build_key(stage, filename)
        s3, bucket = self._s3_client_for_stage(stage)
        response = await asyncio.to_thread(s3.get_object, Bucket=bucket, Key=s3_key)
        body = response["Body"]
        try:
            return await asyncio.to_thread(body.read)
        finally:
            body.close()

    async def delete_file(self, stage: dict, filename: str) -> bool:
        """Delete a file from the stage's S3 path."""
        s3_key = self._build_key(stage, filename)
        s3, bucket = self._s3_client_for_stage(stage)
        await asyncio.to_thread(s3.delete_object, Bucket=bucket, Key=s3_key)
        return True


# Singleton
stage_service = StageService()
