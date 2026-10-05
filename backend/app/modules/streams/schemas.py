from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum


class StreamError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Coverage(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    NOVA_WRITES_ONLY = "NOVA_WRITES_ONLY"
    MANAGED_APPEND = "MANAGED_APPEND"
    FULL_CDC = "FULL_CDC"


class StreamMode(StrEnum):
    APPEND_ONLY = "APPEND_ONLY"
    STANDARD = "STANDARD"


class StreamStatus(StrEnum):
    READY = "READY"
    STALE = "STALE"
    BLOCKED = "BLOCKED"
    SCHEMA_REVIEW_REQUIRED = "SCHEMA_REVIEW_REQUIRED"
    SOURCE_INVALID = "SOURCE_INVALID"


@dataclass(frozen=True, slots=True)
class ChangeCursor:
    epoch: int
    sequence: int

    def __post_init__(self) -> None:
        if any(type(v) is not int or not 0 <= v < 2**63 for v in (self.epoch, self.sequence)):
            raise ValueError("Invalid change cursor")

    def token(self) -> str:
        raw = json.dumps([1, self.epoch, self.sequence], separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def from_token(cls, token: str) -> ChangeCursor:
        try:
            if not isinstance(token, str) or len(token) > 128:
                raise ValueError
            raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
            version, epoch, sequence = json.loads(raw)
            if version != 1:
                raise ValueError
            return cls(epoch, sequence)
        except (ValueError, TypeError, UnicodeError) as exc:
            raise StreamError("STREAM_CURSOR_INVALID", "Invalid Stream cursor") from exc

    def precedes(self, other: ChangeCursor) -> bool:
        if self.epoch != other.epoch:
            raise StreamError("STREAM_SOURCE_INVALID", "Source epoch changed")
        return self.sequence < other.sequence


@dataclass(frozen=True, slots=True)
class ChangeCapabilities:
    coverage: Coverage
    write_fence: bool
    schema_compatible: bool
    governance_supported: bool
    stable_row_id: bool = True
    before_image: bool = False

    def require(self, mode: StreamMode) -> None:
        if not self.write_fence:
            raise StreamError("STREAM_WRITE_FENCE_UNAVAILABLE", "Managed write fence unavailable")
        if not self.schema_compatible:
            raise StreamError("STREAM_SCHEMA_UNSUPPORTED", "Source schema is not supported")
        if not self.governance_supported:
            raise StreamError("STREAM_GOVERNANCE_UNSUPPORTED", "Source governance is not supported")
        if self.coverage not in {Coverage.MANAGED_APPEND, Coverage.FULL_CDC}:
            raise StreamError("STREAM_CHANGE_TRACKING_UNAVAILABLE", "Change capture is unavailable")
        if mode == StreamMode.STANDARD and (
            self.coverage != Coverage.FULL_CDC or not self.before_image
        ):
            raise StreamError(
                "STREAM_MODE_UNSUPPORTED_BY_SOURCE", "Source has no full CDC coverage"
            )


@dataclass(frozen=True, slots=True)
class ChangeSnapshot:
    source_id: str
    after: ChangeCursor
    through: ChangeCursor
    schema_version: int

    def __post_init__(self) -> None:
        if self.through.precedes(self.after):
            raise StreamError("STREAM_CURSOR_INVALID", "Stream interval is reversed")

    @property
    def empty(self) -> bool:
        return self.after == self.through


def operation_id(*facts: str | int) -> str:
    return hashlib.sha256(
        json.dumps(facts, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()


def append_row_id(source_id: str, batch_id: str, ordinal: int) -> str:
    if ordinal < 0:
        raise ValueError("Row ordinal must be nonnegative")
    return operation_id(source_id, batch_id, ordinal)
