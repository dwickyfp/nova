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

from app.core.config import settings


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
    def _s3_client():
        """Create a boto3 S3 client pointed at MinIO.

        Uses root credentials (minioadmin) because service-account credentials
        have compatibility issues with MinIO's _FILE env vars.
        """
        return boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            aws_access_key_id="minioadmin",
            aws_secret_access_key="miniopassword",
            config=BotoConfig(signature_version="s3v4"),
            region_name="us-east-1",
        )

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

        s3_prefix = self._resolve_prefix(stage)
        if prefix:
            s3_prefix = f"{s3_prefix}/{prefix.strip('/')}/"
        else:
            # Ensure trailing slash so delimiter works correctly
            s3_prefix = f"{s3_prefix}/"

        s3 = self._s3_client()
        bucket = settings.S3_BUCKET

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

        s3_key = f"{self._resolve_prefix(stage)}/{filename}"
        s3 = self._s3_client()
        s3.put_object(Bucket=settings.S3_BUCKET, Key=s3_key, Body=content)

        return {"filename": filename, "size": len(content)}

    async def download_file(self, stage_id: str, filename: str) -> bytes:
        """Download a file from the stage's S3 path. Returns raw bytes."""
        stage = await self.get_stage(stage_id)
        if not stage:
            raise ValueError(f"Stage '{stage_id}' not found")

        s3_key = f"{self._resolve_prefix(stage)}/{filename}"
        s3 = self._s3_client()
        response = s3.get_object(Bucket=settings.S3_BUCKET, Key=s3_key)
        return response["Body"].read()

    async def delete_file(self, stage_id: str, filename: str) -> bool:
        """Delete a file from the stage's S3 path."""
        stage = await self.get_stage(stage_id)
        if not stage:
            raise ValueError(f"Stage '{stage_id}' not found")

        s3_key = f"{self._resolve_prefix(stage)}/{filename}"
        s3 = self._s3_client()
        s3.delete_object(Bucket=settings.S3_BUCKET, Key=s3_key)
        return True


# Singleton
stage_service = StageService()
