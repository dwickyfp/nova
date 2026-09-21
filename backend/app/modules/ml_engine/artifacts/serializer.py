"""Versioned, checksummed model-bundle serialization."""

from __future__ import annotations

import hashlib
import io

import joblib

from app.modules.ml_engine.spec import CorruptArtifact


def serialize_bundle(bundle: dict) -> tuple[bytes, str]:
    buffer = io.BytesIO()
    joblib.dump(bundle, buffer, compress=3)
    payload = buffer.getvalue()
    return payload, hashlib.sha256(payload).hexdigest()


def deserialize_bundle(payload: bytes, expected_sha256: str | None = None) -> dict:
    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise CorruptArtifact("Model artifact checksum does not match registry metadata")
    try:
        bundle = joblib.load(io.BytesIO(payload))
    except Exception as exc:
        raise CorruptArtifact(
            f"Model artifact cannot be deserialized: {type(exc).__name__}"
        ) from exc
    if not isinstance(bundle, dict) or "model" not in bundle:
        raise CorruptArtifact("Model artifact has an invalid bundle shape")
    return bundle
