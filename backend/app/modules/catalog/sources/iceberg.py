"""An Iceberg REST catalog as a Nova Catalog source.

A connection names the REST catalog service and the object storage that holds
the table files:

    http[s]://host[:port][/path]?warehouse=...&s3_endpoint=...&s3_region=...

The connection credential is the storage key pair: the access key ID as the
username and the secret access key as the password. The engine sends them only
to the storage service; ``SHOW CREATE CATALOG`` masks them.

The engine reads and writes the tables itself (``"type" = "iceberg"``), so this
source has no JDBC driver. Object kinds come from the REST service, which lists
tables and views separately; the engine reports both as tables.

Not supported yet: REST services that require sign-in (OAuth2 or a bearer
token) and storage credentials vended by the REST service.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, ClassVar
from urllib.parse import parse_qsl, quote, urlsplit

from app.common.sql_guard import is_credential_property

from ..errors import CatalogError
from .base import (
    _HOST,
    _OPTION_VALUE,
    TABLE,
    VIEW,
    CatalogSource,
    Credentials,
    Endpoint,
    ObjectKind,
    ObjectKinds,
)

#: Endpoint options and what they set on the engine catalog.
OPTIONS: dict[str, str] = {
    "warehouse": "iceberg.catalog.warehouse",
    "s3_endpoint": "aws.s3.endpoint",
    "s3_region": "aws.s3.region",
    "s3_path_style": "aws.s3.enable_path_style_access",
}
#: Kept with the endpoint, never sent as a property.
_ADDRESS_OPTIONS = frozenset({"scheme", "path"})

#: Engine catalog properties a caller may add: metadata caching and statistics.
#: The REST address, the storage, and every credential come from the connection.
_ALLOWED_PROPERTY = re.compile(
    r"^(enable_iceberg_metadata_cache|iceberg_manifest_cache_with_column_statistics"
    r"|refresh_iceberg_manifest_min_length|iceberg_data_file_cache_memory_usage_ratio"
    r"|iceberg_delete_file_cache_memory_usage_ratio|iceberg_table_cache_refresh_interval_sec"
    r"|enable_get_stats_from_external_metadata)$"
)
_PATH = re.compile(r"^(/[A-Za-z0-9_.\-]+)*$")
_STORAGE_URL = re.compile(r"^https?://[A-Za-z0-9]([A-Za-z0-9.\-]{0,252}[A-Za-z0-9])?(:\d{1,5})?$")
_REGION = re.compile(r"^[a-z0-9\-]{1,64}$")

#: Seconds a REST metadata read may take.
_REST_TIMEOUT = 15.0


class IcebergSource(CatalogSource):
    connector_type = "iceberg"
    label = "Iceberg"
    default_port = 8181
    endpoint_format = (
        "http[s]://host[:port][/path]?warehouse=LOCATION&s3_endpoint=URL&s3_region=REGION"
    )
    allowed_options: ClassVar[frozenset[str] | None] = frozenset(OPTIONS)
    credential_label = "storage access key and secret key"

    # ── Endpoint ─────────────────────────────────────────────────

    def parse_endpoint(self, value: str) -> Endpoint:
        """Parse the REST catalog URI and its storage options.

        The URI carries no user, password or token; the storage key pair is
        the connection credential.
        """
        text = (value or "").strip()
        if not text or len(text) > 1024 or any(ch.isspace() for ch in text):
            raise CatalogError(f"An Iceberg endpoint is {self.endpoint_format}")
        if "://" not in text:
            text = "http://" + text
        try:
            parts = urlsplit(text)
            port = parts.port
        except ValueError:
            raise CatalogError("The Iceberg catalog URI has an invalid port") from None
        if parts.scheme not in {"http", "https"}:
            raise CatalogError("The Iceberg catalog URI starts with http:// or https://")
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            raise CatalogError("The Iceberg catalog URI cannot carry a user or password")
        if parts.fragment:
            raise CatalogError("The Iceberg catalog URI cannot have a fragment")
        host = parts.hostname or ""
        if not _HOST.match(host):
            raise CatalogError("The Iceberg catalog URI has an invalid host")
        if port is None:
            port = 443 if parts.scheme == "https" else self.default_port
        if not 0 < port < 65536:
            raise CatalogError("The Iceberg catalog URI has an invalid port")
        path = parts.path.rstrip("/")
        if path and not _PATH.match(path):
            raise CatalogError("The Iceberg catalog URI has an invalid path")
        options: dict[str, str] = {}
        if parts.scheme == "https":
            options["scheme"] = "https"
        if path:
            options["path"] = path
        query = parts.query
        for key, option in parse_qsl(query, keep_blank_values=True, strict_parsing=bool(query)):
            if key not in OPTIONS or is_credential_property(key) or key in options:
                raise CatalogError(f"Connection option {key!r} is not accepted")
            if not _OPTION_VALUE.match(option) or not option:
                raise CatalogError(f"Connection option {key!r} has an invalid value")
            options[key] = option
        storage = options.get("s3_endpoint")
        if storage is not None and not _STORAGE_URL.match(storage):
            raise CatalogError(
                "s3_endpoint is the storage service URL, such as http://minio:9000"
            )
        region = options.get("s3_region")
        if region is not None and not _REGION.match(region):
            raise CatalogError("s3_region is a region name, such as us-east-1")
        path_style = options.get("s3_path_style")
        if path_style is not None:
            if path_style.lower() not in {"true", "false"}:
                raise CatalogError("s3_path_style is true or false")
            options["s3_path_style"] = path_style.lower()
        warehouse = options.get("warehouse")
        if warehouse is not None and "@" in warehouse:
            raise CatalogError("The warehouse cannot carry a user or password")
        return Endpoint(host, port, None, options)

    def catalog_uri(self, endpoint: Endpoint) -> str:
        scheme = endpoint.options.get("scheme", "http")
        return f"{scheme}://{endpoint.host}:{endpoint.port}{endpoint.options.get('path', '')}"

    # ── Engine catalog ───────────────────────────────────────────

    def engine_properties(self, endpoint: Endpoint, credentials: Credentials) -> dict[str, str]:
        """The engine's Iceberg REST catalog. Carries the storage secret key."""
        params = {
            "type": "iceberg",
            "iceberg.catalog.type": "rest",
            "iceberg.catalog.uri": self.catalog_uri(endpoint),
        }
        if "warehouse" in endpoint.options:
            params["iceberg.catalog.warehouse"] = endpoint.options["warehouse"]
        if "s3_endpoint" in endpoint.options:
            params["aws.s3.endpoint"] = endpoint.options["s3_endpoint"]
            # S3-compatible storage (MinIO and the like) addresses buckets by path.
            params["aws.s3.enable_path_style_access"] = endpoint.options.get(
                "s3_path_style", "true"
            )
        elif "s3_path_style" in endpoint.options:
            params["aws.s3.enable_path_style_access"] = endpoint.options["s3_path_style"]
        params["aws.s3.region"] = endpoint.options.get("s3_region", "us-east-1")
        params["aws.s3.use_instance_profile"] = "false"
        params["aws.s3.access_key"] = credentials.username
        params["aws.s3.secret_key"] = credentials.password
        return params

    def check_properties(self, properties: dict[str, str]) -> dict[str, str]:
        for key in properties:
            if is_credential_property(key) or not _ALLOWED_PROPERTY.match(key):
                raise CatalogError(
                    f"Property '{key}' cannot be set on an Iceberg catalog; the catalog "
                    "service, storage, and credentials come from the connection"
                )
        return dict(properties)

    # ── Source metadata ──────────────────────────────────────────

    async def read_objects(
        self, endpoint: Endpoint, credentials: Credentials, databases: Sequence[str]
    ) -> ObjectKinds:
        """Tables and views of each namespace, from the REST service."""
        import httpx

        del credentials  # storage keys; the REST service takes none
        if not databases:
            return {}
        scheme = endpoint.options.get("scheme", "http")
        path = endpoint.options.get("path", "")
        base = f"{scheme}://{endpoint.direct_host}:{endpoint.port}{path}"
        kinds: ObjectKinds = {}
        async with httpx.AsyncClient(base_url=base, timeout=_REST_TIMEOUT) as client:
            params = (
                {"warehouse": endpoint.options["warehouse"]}
                if "warehouse" in endpoint.options else None
            )
            config = await _json(await client.get("/v1/config", params=params))
            prefix = str((config.get("overrides") or {}).get("prefix") or "").strip("/")
            root = f"/v1/{quote(prefix, safe='/')}" if prefix else "/v1"
            for database in databases:
                namespace = quote(database, safe="")
                for group, kind in (
                    ("tables", ObjectKind(TABLE, "Iceberg table")),
                    ("views", ObjectKind(VIEW, "Iceberg view")),
                ):
                    listing = f"{root}/namespaces/{namespace}/{group}"
                    for name in await _identifiers(client, listing):
                        kinds[(database, name)] = kind
        return kinds


async def _json(response: Any) -> dict[str, Any]:
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else {}


async def _identifiers(client: Any, path: str) -> list[str]:
    """Every identifier name a REST list endpoint returns, following page tokens.

    A service without view support answers the views list with 404 or 400.
    """
    names: list[str] = []
    token: str | None = None
    for _ in range(1000):
        response = await client.get(path, params={"pageToken": token} if token else None)
        if response.status_code in {400, 404, 405, 501} and path.endswith("/views"):
            return names
        body = await _json(response)
        names.extend(
            str(item.get("name")) for item in body.get("identifiers") or [] if item.get("name")
        )
        token = body.get("next-page-token")
        if not token:
            break
    return names
