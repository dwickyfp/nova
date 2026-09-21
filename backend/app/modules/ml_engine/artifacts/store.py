"""Object-store backed model artifacts; registry rows hold only opaque URIs."""

from __future__ import annotations

from typing import Protocol

import boto3
from botocore.client import Config as BotoConfig

from app.core.config import get_storage_connection, settings
from app.modules.query.dialect.injector import resolve_storage_credentials


class ArtifactStore(Protocol):
    def put(self, key: str, payload: bytes) -> str: ...
    def get(self, uri: str) -> bytes: ...
    def delete(self, uri: str) -> None: ...


class ObjectArtifactStore:
    """Store artifacts in Nova's configured storage without exposing provider details."""

    scheme = "nova-artifact"

    def __init__(self, connection_name: str | None = None) -> None:
        self.connection_name = connection_name or settings.ML_ARTIFACT_STORAGE_CONNECTION

    def _connection(self):
        return get_storage_connection(self.connection_name)

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
        self._client().put_object(Bucket=self._connection().bucket, Key=normalized, Body=payload)
        return f"{self.scheme}://{self.connection_name}/{normalized}"

    def get(self, uri: str) -> bytes:
        connection_name, key = self._parse(uri)
        if connection_name != self.connection_name:
            return ObjectArtifactStore(connection_name).get(uri)
        return self._client().get_object(Bucket=self._connection().bucket, Key=key)["Body"].read()

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

    def put(self, key: str, payload: bytes) -> str:
        uri = f"memory://{key.strip('/')}"
        self.objects[uri] = payload
        return uri

    def get(self, uri: str) -> bytes:
        return self.objects[uri]

    def delete(self, uri: str) -> None:
        self.objects.pop(uri, None)
