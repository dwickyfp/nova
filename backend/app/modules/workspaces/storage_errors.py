"""Translate boto3/botocore failures into classified, credential-safe errors.

Why this exists (NOVA-137): the workspace service originally caught only
``ClientError`` and re-raised ``StorageError`` with ``str(exc)``, which is the
raw botocore message. Two problems fell out of that:

1. ``EndpointConnectionError`` (endpoint down / DNS failure) is a
   ``BotoCoreError``, **not** a ``ClientError``, so it escaped the handler
   entirely and surfaced as a bare 500 from the global catch-all.
2. Even the caught path leaked: botocore's message echoes the endpoint URL and,
   for signature failures, the access key id — which AGENTS.md §2 forbids in
   API responses, logs and error messages.

This module is the single translation point. It inspects the exception's
*type* and error *code* (never its message) and returns a Nova-authored
summary plus the right HTTP status. The underlying exception is still chained
(``from exc``) so the server-side traceback keeps the detail, but its text is
never copied into the client-facing message.
"""

from __future__ import annotations

from botocore.exceptions import BotoCoreError, ClientError

from app.core.exceptions import StorageError, StorageUnavailableError

#: S3 error codes that mean "the request could not reach / be accepted by the
#: storage service" — the dependency, not Nova, is at fault.
_UNAVAILABLE_CODES = frozenset(
    {
        "ServiceUnavailable",
        "InternalError",
        "SlowDown",
        "RequestTimeout",
        "RequestTimeTooSkewed",
        "SignatureDoesNotMatch",
        "InvalidAccessKeyId",
        "AccessDenied",
        "AuthorizationHeaderMalformed",
        "InvalidBucketName",
    }
)

#: S3 error codes for "the object/bucket is not there".
_NOT_FOUND_CODES = frozenset({"NoSuchBucket", "NoSuchKey", "404"})


def classify_storage_error(exc: BaseException, *, action: str) -> StorageError:
    """Map a botocore/boto3 exception to a classified ``StorageError``.

    ``action`` is a short verb phrase (e.g. ``"save"``, ``"read"``) used to
    build the message. The returned message never contains the original
    exception text, so neither the endpoint nor the credentials can leak.

    ``BotoCoreError`` covers the client-side failures that were escaping:
    ``EndpointConnectionError``, ``ConnectTimeoutError``,
    ``NoCredentialsError``, ``ReadTimeoutError`` and friends. They all mean the
    same thing to the caller: storage is not usable right now.
    """
    if isinstance(exc, ClientError):
        code = _error_code(exc)
        if code in _NOT_FOUND_CODES:
            return StorageError(
                f"Cannot {action} workspace file: the storage bucket or object "
                "does not exist. An administrator must create it.",
                status_code=404,
            )
        if code in _UNAVAILABLE_CODES:
            return StorageUnavailableError(
                f"Cannot {action} workspace file: the storage backend rejected "
                "the request. Check the storage connection and credentials."
            )
        return StorageError(
            f"Cannot {action} workspace file: the storage backend returned an "
            "error. See the server logs for details.",
            status_code=502,
        )

    if isinstance(exc, BotoCoreError):
        return StorageUnavailableError(
            f"Cannot {action} workspace file: the storage backend is "
            "unreachable. Check the storage endpoint and that it is running."
        )

    return StorageError(
        f"Cannot {action} workspace file: unexpected storage failure. "
        "See the server logs for details.",
        status_code=502,
    )


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", "")) if exc.response else ""
