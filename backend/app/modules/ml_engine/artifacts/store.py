"""Object-store backed model artifacts; registry rows hold only opaque URIs."""

from __future__ import annotations

import hashlib
import logging
from typing import Protocol
from uuid import uuid4

import boto3
from botocore.client import Config as BotoConfig

from app.core.config import get_storage_connection, settings
from app.modules.query.dialect.injector import resolve_storage_credentials

logger = logging.getLogger(__name__)


class ArtifactStore(Protocol):
    def uri_for_key(self, key: str) -> str: ...
    def cleanup_upload(self, uri: str, *, keep_final: bool = False) -> None: ...
    def cleanup_result(self, manifest_uri: str) -> None: ...
    def put(self, key: str, payload: bytes) -> str: ...
    def get(self, uri: str) -> bytes: ...
    def delete(self, uri: str) -> None: ...
    def copy(self, uri: str, key: str, checksum: str) -> str: ...


class ObjectArtifactStore:
    """Store artifacts in Nova's configured storage without exposing provider details."""

    scheme = "nova-artifact"

    def __init__(self, connection_name: str | None = None) -> None:
        self.connection_name = connection_name or settings.ML_ARTIFACT_STORAGE_CONNECTION

    def _connection(self):
        return get_storage_connection(self.connection_name)

    def uri_for_key(self, key: str) -> str:
        return f"{self.scheme}://{self.connection_name}/{key.strip('/')}"

    def cleanup_upload(self, uri: str, *, keep_final: bool = False) -> None:
        connection, key = self._parse(uri)
        if connection != self.connection_name:
            ObjectArtifactStore(connection).cleanup_upload(uri, keep_final=keep_final)
            return
        client = self._client()
        bucket = self._connection().bucket
        # This exact destination's staging objects belong to the same upload lease.
        for page in client.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=f"{key}.upload-"
        ):
            for item in page.get("Contents", []):
                client.delete_object(Bucket=bucket, Key=item["Key"])
        if not keep_final:
            self.delete(uri)

    def cleanup_result(self, manifest_uri: str) -> None:
        connection, key = self._parse(manifest_uri)
        if not key.endswith("/manifest.json") or "/results/" not in f"/{key}":
            raise ValueError("Invalid result cleanup destination")
        if connection != self.connection_name:
            ObjectArtifactStore(connection).cleanup_result(manifest_uri)
            return
        client = self._client()
        bucket = self._connection().bucket
        prefix = key.rsplit("/", 1)[0] + "/"
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                client.delete_object(Bucket=bucket, Key=item["Key"])

    def _client(self):
        connection = self._connection()
        access_key, secret_key = resolve_storage_credentials(self.connection_name)
        return boto3.client(
            "s3",
            endpoint_url=connection.endpoint or settings.S3_ENDPOINT,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            region_name=connection.region or "us-east-1",
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path" if connection.path_style else "virtual"},
            ),
        )

    def put(self, key: str, payload: bytes) -> str:
        normalized = key.strip("/")
        temporary = f"{normalized}.upload-{uuid4().hex}"
        client = self._client()
        bucket = self._connection().bucket
        try:
            client.put_object(Bucket=bucket, Key=temporary, Body=payload)
            stored_size = int(client.head_object(Bucket=bucket, Key=temporary)["ContentLength"])
            if stored_size != len(payload):
                raise OSError(
                    f"Artifact upload size mismatch: expected {len(payload)}, got {stored_size}"
                )
            body = client.get_object(Bucket=bucket, Key=temporary)["Body"]
            digest = hashlib.sha256()
            try:
                for chunk in iter(lambda: body.read(1024 * 1024), b""):
                    digest.update(chunk)
            finally:
                body.close()
            if digest.digest() != hashlib.sha256(payload).digest():
                raise OSError("Artifact upload checksum mismatch")
            client.copy_object(
                Bucket=bucket,
                Key=normalized,
                CopySource={"Bucket": bucket, "Key": temporary},
            )
        finally:
            try:
                client.delete_object(Bucket=bucket, Key=temporary)
            except Exception:
                logger.warning("Temporary ML upload cleanup failed")
        return f"{self.scheme}://{self.connection_name}/{normalized}"

    def get(self, uri: str) -> bytes:
        connection_name, key = self._parse(uri)
        if connection_name != self.connection_name:
            return ObjectArtifactStore(connection_name).get(uri)
        body = self._client().get_object(Bucket=self._connection().bucket, Key=key)["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def copy(self, uri: str, key: str, checksum: str) -> str:
        connection, source_key = self._parse(uri)
        if connection != self.connection_name:
            raise ValueError("Artifact promotion must stay in the configured storage connection")
        client = self._client()
        bucket = self._connection().bucket
        body = client.get_object(Bucket=bucket, Key=source_key)["Body"]
        digest = hashlib.sha256()
        try:
            for chunk in iter(lambda: body.read(1024 * 1024), b""):
                digest.update(chunk)
        finally:
            body.close()
        if digest.hexdigest() != checksum:
            raise ValueError("Artifact promotion checksum mismatch")
        client.copy_object(Bucket=bucket, Key=key, CopySource={"Bucket": bucket, "Key": source_key})
        return f"{self.scheme}://{connection}/{key}"

    def delete(self, uri: str) -> None:
        connection_name, key = self._parse(uri)
        if connection_name != self.connection_name:
            ObjectArtifactStore(connection_name).delete(uri)
            return
        self._client().delete_object(Bucket=self._connection().bucket, Key=key)

    @classmethod
    def _parse(cls, uri: str) -> tuple[str, str]:
        prefix = f"{cls.scheme}://"
        if not uri.startswith(prefix):
            raise ValueError("Unsupported model artifact URI")
        connection, separator, key = uri[len(prefix) :].partition("/")
        if not separator or not connection or not key or ".." in key.split("/"):
            raise ValueError("Invalid model artifact URI")
        return connection, key


class MemoryArtifactStore:
    """In-memory artifact store for deterministic tests and local smoke runs."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def uri_for_key(self, key: str) -> str:
        return f"memory://{key.strip('/')}"

    def cleanup_upload(self, uri: str, *, keep_final: bool = False) -> None:
        for key in list(self.objects):
            if key.startswith(f"{uri}.upload-") or (key == uri and not keep_final):
                self.delete(key)

    def cleanup_result(self, manifest_uri: str) -> None:
        prefix = manifest_uri.rsplit("/", 1)[0] + "/"
        for key in list(self.objects):
            if key.startswith(prefix):
                self.delete(key)

    def put(self, key: str, payload: bytes) -> str:
        uri = f"memory://{key.strip('/')}"
        self.objects[uri] = payload
        return uri

    def get(self, uri: str) -> bytes:
        return self.objects[uri]

    def delete(self, uri: str) -> None:
        self.objects.pop(uri, None)

    def copy(self, uri: str, key: str, checksum: str) -> str:
        payload = self.get(uri)
        if hashlib.sha256(payload).hexdigest() != checksum:
            raise ValueError("Artifact promotion checksum mismatch")
        return self.put(key, payload)
