"""Stage Manager service — CRUD for stages + file operations via MinIO/S3.

Stages are registered in NOVA_SYSTEM.CONFIG_STAGES and backed by an S3-compatible
object store (MinIO by default). Files are listed/uploaded/downloaded/deleted
through the boto3 S3 client.
"""

from uuid import uuid4

import asyncmy
import asyncmy.cursors
import boto3
from botocore.client import Config as BotoConfig

from app.core.config import get_storage_connection, settings
from app.modules.query.dialect.injector import resolve_storage_credentials


class StagePathError(ValueError):
    """A user-supplied stage path is malformed or escapes the stage prefix.

    Subclasses ``ValueError`` so existing ``except ValueError`` callers keep
    working, while giving the router a distinct type to map to 400 rather than
    the 404 used for a missing stage/file (NOVA-106).
    """


class StageService:
    """Business logic for stage management and file operations."""

    # ── DB helpers ──────────────────────────────────────────────

    @staticmethod
    async def _connect() -> asyncmy.Connection:
        """Create a direct asyncmy connection to StarRocks."""
        return await asyncmy.connect(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user="root",
            password="",
            autocommit=True,
        )

    # ── Stage CRUD ──────────────────────────────────────────────

    async def list_stages(self) -> list[dict]:
        """List all registered stages."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, name, database_name, schema_name, "
                    "storage_connection, base_prefix, created_at, created_by "
                    "FROM NOVA_SYSTEM.CONFIG_STAGES ORDER BY name"
                )
                rows = await cur.fetchall()
                return list(rows)
        finally:
            conn.close()

    async def get_stage(self, stage_id: str) -> dict | None:
        """Get a single stage by ID."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, name, database_name, schema_name, "
                    "storage_connection, base_prefix, created_at, created_by "
                    "FROM NOVA_SYSTEM.CONFIG_STAGES WHERE id = %s",
                    (stage_id,),
                )
                row = await cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    async def create_stage(self, data: dict, username: str) -> dict | None:
        """INSERT a new stage into CONFIG_STAGES. Returns the created stage."""
        stage_id = str(uuid4())
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "INSERT INTO NOVA_SYSTEM.CONFIG_STAGES "
                    "(id, name, database_name, schema_name, storage_connection, "
                    "base_prefix, created_at, created_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s)",
                    (
                        stage_id,
                        data["name"],
                        data["database_name"],
                        data["schema_name"],
                        data["storage_connection"],
                        data.get("base_prefix", ""),
                        username,
                    ),
                )
            return await self.get_stage(stage_id)
        finally:
            conn.close()

    async def delete_stage(self, stage_id: str) -> bool:
        """DELETE a stage by ID. Returns True if a row was deleted."""
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM NOVA_SYSTEM.CONFIG_STAGES WHERE id = %s",
                    (stage_id,),
                )
                return cur.rowcount > 0
        finally:
            conn.close()

    # ── S3 / MinIO client ──────────────────────────────────────

    @staticmethod
    def _s3_client(storage_connection: str | None = None):
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
        conn = get_storage_connection(storage_connection)
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
        connection = stage.get("storage_connection") or None
        conn = get_storage_connection(connection)
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

    async def list_files(self, stage_id: str, prefix: str = "") -> list[dict]:
        """List files under the stage's S3 path (non-recursive, delimited '/')."""
        stage = await self.get_stage(stage_id)
        if not stage:
            raise ValueError(f"Stage '{stage_id}' not found")

        # Trailing slash is required for the delimiter to work correctly.
        base_prefix = self._resolve_prefix(stage)
        if prefix:
            safe_prefix = self._safe_relative_path(prefix, field="prefix")
            s3_prefix = f"{base_prefix}/{safe_prefix}/"
        else:
            s3_prefix = f"{base_prefix}/"

        s3, bucket = self._s3_client_for_stage(stage)

        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=bucket,
            Prefix=s3_prefix,
            Delimiter="/",
        )

        files: list[dict] = []
        base_len = len(s3_prefix)
        for page in pages:
            # Common prefixes = "directories"
            for cp in page.get("CommonPrefixes", []):
                name = cp["Prefix"][base_len:].rstrip("/")
                files.append({
                    "name": name,
                    "size": 0,
                    "last_modified": None,
                    "is_dir": True,
                })
            # Objects = files
            for obj in page.get("Contents", []):
                key = obj["Key"]
                # Skip the prefix itself if it appears as an object
                if key == s3_prefix or not key[base_len:]:
                    continue
                files.append({
                    "name": key[base_len:],
                    "size": obj["Size"],
                    "last_modified": obj["LastModified"].isoformat(),
                    "is_dir": False,
                })

        return files

    async def upload_file(self, stage_id: str, filename: str, content: bytes) -> dict:
        """Upload a file to the stage's S3 path."""
        stage = await self.get_stage(stage_id)
        if not stage:
            raise ValueError(f"Stage '{stage_id}' not found")

        s3_key = self._build_key(stage, filename)
        s3, bucket = self._s3_client_for_stage(stage)
        s3.put_object(Bucket=bucket, Key=s3_key, Body=content)

        return {"filename": filename, "size": len(content)}

    async def download_file(self, stage_id: str, filename: str) -> bytes:
        """Download a file from the stage's S3 path. Returns raw bytes."""
        stage = await self.get_stage(stage_id)
        if not stage:
            raise ValueError(f"Stage '{stage_id}' not found")

        s3_key = self._build_key(stage, filename)
        s3, bucket = self._s3_client_for_stage(stage)
        response = s3.get_object(Bucket=bucket, Key=s3_key)
        return response["Body"].read()

    async def delete_file(self, stage_id: str, filename: str) -> bool:
        """Delete a file from the stage's S3 path."""
        stage = await self.get_stage(stage_id)
        if not stage:
            raise ValueError(f"Stage '{stage_id}' not found")

        s3_key = self._build_key(stage, filename)
        s3, bucket = self._s3_client_for_stage(stage)
        s3.delete_object(Bucket=bucket, Key=s3_key)
        return True


# Singleton
stage_service = StageService()
