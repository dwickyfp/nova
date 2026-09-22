"""Versioned, checksummed model-bundle serialization."""

from __future__ import annotations

import hashlib
import io

import joblib

from app.modules.ml_engine.spec import CorruptArtifact

SERIALIZATION_FORMAT = "joblib"
SERIALIZATION_VERSION = 1


def serialize_bundle(bundle: dict) -> tuple[bytes, str]:
    buffer = io.BytesIO()
    joblib.dump(
        {
            "__nova_artifact__": {
                "format": SERIALIZATION_FORMAT,
                "version": SERIALIZATION_VERSION,
            },
            "bundle": bundle,
        },
        buffer,
        compress=3,
    )
    payload = buffer.getvalue()
    return payload, hashlib.sha256(payload).hexdigest()


def deserialize_bundle(payload: bytes, expected_sha256: str | None = None) -> dict:
    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise CorruptArtifact("Model artifact checksum does not match registry metadata")
    try:
        decoded = joblib.load(io.BytesIO(payload))
    except Exception as exc:
        raise CorruptArtifact(
            f"Model artifact cannot be deserialized: {type(exc).__name__}"
        ) from exc
    if isinstance(decoded, dict) and "__nova_artifact__" in decoded:
        metadata = decoded["__nova_artifact__"]
        if metadata != {"format": SERIALIZATION_FORMAT, "version": SERIALIZATION_VERSION}:
            raise CorruptArtifact("Model artifact serialization format is incompatible")
        bundle = decoded.get("bundle")
    else:
        # Legacy artifacts predate the envelope and remain readable.
        bundle = decoded
    if not isinstance(bundle, dict) or "model" not in bundle:
        raise CorruptArtifact("Model artifact has an invalid bundle shape")
    return bundle
