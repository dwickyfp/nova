"""Stage Manager service — CRUD for stages + file operations via MinIO/S3.

Stages are registered in NOVA_SYSTEM.CONFIG_STAGES and backed by an S3-compatible
object store (MinIO by default). Files are listed/uploaded/downloaded/deleted
through the boto3 S3 client.
"""

import posixpath
from uuid import uuid4

import asyncmy
import asyncmy.cursors
import boto3
from botocore.client import Config as BotoConfig

from app.core.config import get_storage_connection, settings
from app.modules.query.dialect.injector import resolve_storage_credentials


class InvalidStagePathError(ValueError):
    """A caller-supplied stage path is empty or escapes the stage prefix.

    A ``ValueError`` subclass so the pre-existing ``except ValueError`` call
    sites (notably ``router.py``) keep mapping a not-found *or* a rejected
    path onto the same client error, while callers that care can distinguish
    the two. Raised only *after* validation — no partial S3 key is ever built.
    """


def _validate_stage_path(filename: str) -> str:
    """Normalize a caller-supplied stage-relative path and bound it to the prefix.

    The stage file operations concatenate ``filename`` straight onto the
    stage's S3 prefix. Without this, ``../`` segments climb out of the prefix
    and let a caller read or delete any object the stage's principal can reach
    in the bucket (the ``filename:path`` route parameter reads ``a/b/../../x``
    verbatim, and ``ftypename`` of an upload is equally attacker-controlled).

    Accepts a slash-separated relative path — including nested sub-folders
    inside the stage — and rejects everything that is not one:

    * empty, ``.``, ``/`` (no object is addressed)
    * absolute paths (``/etc/passwd``)
    * traversal segments (``..``)
    * Windows-style backslashes and NUL bytes

    Returns the normalized relative key (``folder/../report.csv`` →
    ``report.csv``) so the caller builds exactly the key it validated. Raises
    :class:`InvalidStagePathError` before any key is formed.
    """
    if not isinstance(filename, str) or not filename:
        raise InvalidStagePathError("Stage path must be a non-empty string")

    if "\x00" in filename:
        raise InvalidStagePathError("Stage path contains a NUL byte")

    if "\\" in filename:
        raise InvalidStagePathError(f"Stage path '{filename}' uses a backslash separator")

    if filename.startswith("/"):
        raise InvalidStagePathError(
            f"Stage path '{filename}' is absolute; it must be relative to the stage"
        )

    # ``normpath`` collapses ``.`` and ``..``; ``root`` is discarded because an
    # absolute path was already rejected, so it only ever reads ".". A result
    # that is still ``..``-prefixed (or exactly ``.``/``..``) climbed above the
    # stage root and is refused.
    normalized = posixpath.normpath(filename)
    if normalized == "." or normalized == ".." or normalized.startswith("../"):
        raise InvalidStagePathError(f"Stage path '{filename}' escapes the stage prefix")

    return normalized


def _resolve_stage_key(stage: dict, filename: str) -> str:
    """Resolve a validated ``filename`` into a key that stays inside the stage.

    Both sides are normalized before the prefix check: ``base_prefix`` is
    registered data but could itself carry a traversal segment (NOVA-106 AC4),
    and ``filename`` is untrusted. The final prefix check is the invariant the
    whole fix rests on — the resolved key is the stage prefix or sits below it.

    Validating the *resolved* key rather than only the caller's ``filename``
    matters: ``normpath`` collapses ``.`` and ``..`` against the prefix, so a
    filename that looks benign on its own can still shift the boundary once
    joined. Checking the joined result is what makes the guarantee hold
    regardless of how either side was shaped.
    """
    prefix = posixpath.normpath(StageService._resolve_prefix(stage))
    normalized = _validate_stage_path(filename)
    key = f"{prefix}/{normalized}"

    if key != prefix and not key.startswith(f"{prefix}/"):
        raise InvalidStagePathError(f"Stage path '{filename}' escapes the stage prefix")
    return key


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

    # ── File operations ─────────────────────────────────────────

    async def list_files(self, stage_id: str, prefix: str = "") -> list[dict]:
        """List files under the stage's S3 path (non-recursive, delimited '/')."""
        stage = await self.get_stage(stage_id)
        if not stage:
            raise ValueError(f"Stage '{stage_id}' not found")

        # Trailing slash is required for the delimiter to work correctly. The
        # prefix is validated and normalized like every other caller-supplied
        # path, so browsing cannot walk out of the stage either (NOVA-106 AC4).
        s3_prefix = (
            f"{_resolve_stage_key(stage, prefix)}/" if prefix else f"{self._resolve_prefix(stage)}/"
        )

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

        s3_key = _resolve_stage_key(stage, filename)
        s3, bucket = self._s3_client_for_stage(stage)
        s3.put_object(Bucket=bucket, Key=s3_key, Body=content)

        return {"filename": filename, "size": len(content)}

    async def download_file(self, stage_id: str, filename: str) -> bytes:
        """Download a file from the stage's S3 path. Returns raw bytes."""
        stage = await self.get_stage(stage_id)
        if not stage:
            raise ValueError(f"Stage '{stage_id}' not found")

        s3_key = _resolve_stage_key(stage, filename)
        s3, bucket = self._s3_client_for_stage(stage)
        response = s3.get_object(Bucket=bucket, Key=s3_key)
        return response["Body"].read()

    async def delete_file(self, stage_id: str, filename: str) -> bool:
        """Delete a file from the stage's S3 path."""
        stage = await self.get_stage(stage_id)
        if not stage:
            raise ValueError(f"Stage '{stage_id}' not found")

        s3_key = _resolve_stage_key(stage, filename)
        s3, bucket = self._s3_client_for_stage(stage)
        s3.delete_object(Bucket=bucket, Key=s3_key)
        return True


# Singleton
stage_service = StageService()
