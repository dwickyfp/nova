"""Idempotent bootstrap of the workspace object-storage bucket.

The workspace feature writes every SQL file to a bucket named by the storage
connection config (``nova.yaml`` → ``workspace.storage_connection`` →
``bucket``). Previously nothing created that bucket: a fresh MinIO/S3 started
empty, and the first ``POST /workspaces/files`` failed with ``NoSuchBucket``.
The fix belongs at startup, not on the request path — creating a bucket per
write would hide the misconfiguration and add latency to the first save.

Idempotency: ``head_bucket`` is the fast path (one HEAD). When it 404s,
``create_bucket`` runs; a concurrent creator surfaces as
``BucketAlreadyOwnedByYou``/``BucketAlreadyExists``, which is success for our
purposes, so those are swallowed — and *only* those. Any other error is
classified and re-raised, never ``except Exception: pass``.

Credentials never appear in what this module returns or logs: it only logs the
bucket *name* and the classified reason.
"""

from __future__ import annotations

import logging

from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import get_storage_connection, load_nova_app_config
from app.modules.workspaces.service import workspace_service
from app.modules.workspaces.storage_errors import classify_storage_error

logger = logging.getLogger(__name__)

#: Raised by MinIO/S3 when the bucket already exists (ours or someone else's).
_ALREADY_EXISTS_CODES = frozenset({"BucketAlreadyOwnedByYou", "BucketAlreadyExists"})


def ensure_workspace_bucket() -> None:
    """Create the workspace bucket if it does not exist. Idempotent.

    Raises:
        StorageError: classified failure if the bucket cannot be reached or
            created. The caller decides whether that is fatal; at startup it is
            logged, never fatal, because the rest of Nova (query, explorer)
            does not depend on object storage.
    """
    config = load_nova_app_config()
    bucket = get_storage_connection(config.workspace.storage_connection).bucket
    client = workspace_service._client()

    try:
        client.head_bucket(Bucket=bucket)
        return
    except ClientError as exc:
        if _error_code(exc) != "404":
            raise classify_storage_error(exc, action="initialise") from exc
    except BotoCoreError as exc:
        raise classify_storage_error(exc, action="initialise") from exc

    try:
        client.create_bucket(Bucket=bucket)
        logger.info("Created workspace storage bucket %r", bucket)
    except ClientError as exc:
        if _error_code(exc) in _ALREADY_EXISTS_CODES:
            return
        raise classify_storage_error(exc, action="initialise") from exc
    except BotoCoreError as exc:
        raise classify_storage_error(exc, action="initialise") from exc


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", "")) if exc.response else ""
