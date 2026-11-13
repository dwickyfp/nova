"""Export, conflict and validation manifests for the one-shot metadata import.

Everything here is pure: canonical value encoding, row fingerprints, table
digests and the file/hash contract that epoch publication verifies. Manifests
carry counts, digests and opaque row addresses, never source values.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

EXPORT_FORMAT = "nova-metadata-export/v1"
VALIDATION_FORMAT = "nova-metadata-import-validation/v1"
CONFLICT_FORMAT = "nova-metadata-import-conflicts/v1"
VALIDATED = "VALIDATED"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_CHUNK_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,62}/[0-9]{6}\.jsonl\.gz")
_DATETIME = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{6}))?"
)
_MAX_MANIFEST_BYTES = 64 * 1024 * 1024


class ManifestError(ValueError):
    """A manifest or chunk is missing, altered or outside the contract."""

    def __init__(self, reason: str = "invalid") -> None:
        # Reasons are fixed identifiers chosen by this module, never values.
        self.reason = reason
        super().__init__(f"Metadata import manifest is invalid: {reason}")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_private(path: Path, data: bytes) -> None:
    """Write atomically with owner-only permissions."""
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


# ── Canonical source values ────────────────────────────────────────────────
# Chunks store each source value as JSON: text/JSON as the exact string,
# integers as integers, booleans as 0/1, doubles as repr() text and DATETIME
# as zone-free "YYYY-MM-DD HH:MM:SS[.ffffff]" exactly as the engine returned it.


def encode_source_value(source_type: str, value: Any) -> Any:
    """Encode a StarRocks driver value without loss; refuse unknown shapes."""
    if value is None:
        return None
    if source_type == "datetime":
        if not isinstance(value, datetime) or value.tzinfo is not None:
            raise ManifestError("source_datetime_shape")
        return value.isoformat(sep=" ")
    if source_type == "double":
        if type(value) is not float:
            raise ManifestError("source_double_shape")
        return repr(value)
    if source_type in ("int(11)", "bigint(20)", "tinyint(1)"):
        if type(value) is bool:
            return int(value)
        if type(value) is not int:
            raise ManifestError("source_integer_shape")
        return value
    if isinstance(value, bytes | bytearray):
        try:
            value = bytes(value).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise ManifestError("source_text_encoding") from None
    if not isinstance(value, str):
        raise ManifestError("source_text_shape")
    return value


def decode_source_value(source_type: str, encoded: Any) -> Any:
    """Return the driver-shaped value that ``ImportColumn.convert`` validates."""
    if encoded is None:
        return None
    if source_type == "datetime":
        match = _DATETIME.fullmatch(encoded) if isinstance(encoded, str) else None
        if not match:
            raise ManifestError("chunk_datetime_shape")
        parts = [int(part) for part in match.groups()[:6]]
        micro = int(match[7]) if match[7] else 0
        try:
            return datetime(parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], micro)
        except ValueError:
            raise ManifestError("chunk_datetime_value") from None
    if source_type == "double":
        if not isinstance(encoded, str):
            raise ManifestError("chunk_double_shape")
        try:
            return float(encoded)
        except ValueError:
            raise ManifestError("chunk_double_shape") from None
    return encoded


def _canonical_json_number(number: Decimal) -> str:
    if not number.is_finite():
        raise ManifestError("json_number")
    text = format(number, "f")
    # PostgreSQL numeric has no negative zero; keep the scale ("-0.0" -> "0.0").
    return text.lstrip("-") if number.is_zero() else text


def _canonical_json_value(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, Decimal):
        return _canonical_json_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(_canonical_json_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                json.dumps(key, ensure_ascii=False) + ":" + _canonical_json_value(value[key])
                for key in sorted(value)
            )
            + "}"
        )
    raise ManifestError("json_shape")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            raise ManifestError("json_duplicate_key")
        result[key] = item
    return result


def canonical_json_text(text: str) -> str:
    """JSON text in the form both StarRocks text and PostgreSQL jsonb reduce to.

    Object keys are sorted, whitespace removed and numbers printed exactly in
    positional notation (jsonb keeps numeric scale but not exponent spelling).
    """

    def refuse(_constant: str) -> Any:
        raise ManifestError("json_constant")

    try:
        parsed = json.loads(
            text,
            parse_int=Decimal,
            parse_float=Decimal,
            object_pairs_hook=_unique_pairs,
            parse_constant=refuse,
        )
    except (ValueError, RecursionError):
        raise ManifestError("json_text") from None
    return _canonical_json_value(parsed)


def fingerprint_value(source_type: str, encoded: Any) -> Any:
    if encoded is None:
        return None
    if source_type == "json":
        return canonical_json_text(encoded)
    if source_type == "double":
        number = float(encoded)
        if not math.isfinite(number):
            raise ManifestError("double_value")
        return repr(number)
    return encoded


def row_fingerprint(source_types: Sequence[str], encoded_row: Sequence[Any]) -> str:
    if len(source_types) != len(encoded_row):
        raise ManifestError("row_width")
    return sha256_bytes(
        canonical_json(
            [
                fingerprint_value(kind, value)
                for kind, value in zip(source_types, encoded_row, strict=True)
            ]
        )
    )


def key_fingerprint(key: Sequence[Any]) -> str:
    return sha256_bytes(canonical_json(list(key)))


class TableDigest:
    """Order-independent multiset digests of keys and full rows."""

    def __init__(self) -> None:
        self._keys: list[str] = []
        self._rows: list[str] = []

    def add(self, key: str, row: str) -> None:
        self._keys.append(key)
        self._rows.append(row)

    @property
    def rows(self) -> int:
        return len(self._rows)

    @property
    def duplicate_keys(self) -> int:
        return len(self._keys) - len(set(self._keys))

    def finish(self) -> dict[str, Any]:
        return {
            "rows": len(self._rows),
            "key_digest": sha256_bytes("\n".join(sorted(self._keys)).encode()),
            "row_digest": sha256_bytes("\n".join(sorted(self._rows)).encode()),
            "duplicate_keys": self.duplicate_keys,
        }


# ── Chunk files ────────────────────────────────────────────────────────────


def chunk_name(table: str, index: int) -> str:
    return f"{table}/{index:06d}.jsonl.gz"


def encode_chunk(rows: Iterable[Sequence[Any]]) -> bytes:
    return b"".join(canonical_json(list(row)) + b"\n" for row in rows)


def write_chunk(directory: Path, name: str, payload: bytes) -> dict[str, Any]:
    if not _CHUNK_NAME.fullmatch(name):
        raise ManifestError("chunk_name")
    path = directory / name
    private_directory(path.parent)
    # mtime=0 keeps the compressed bytes reproducible for the same payload.
    write_private(path, gzip.compress(payload, compresslevel=6, mtime=0))
    return {"file": name, "sha256": sha256_bytes(payload), "gzip_sha256": sha256_file(path)}


def read_chunk(directory: Path, entry: dict[str, Any], width: int) -> list[list[Any]]:
    """Read one chunk and verify both its stored and decompressed digests."""
    name = entry.get("file")
    if not isinstance(name, str) or not _CHUNK_NAME.fullmatch(name):
        raise ManifestError("chunk_name")
    path = directory / name
    try:
        if sha256_file(path) != entry.get("gzip_sha256"):
            raise ManifestError("chunk_altered")
        payload = gzip.decompress(path.read_bytes())
    except (OSError, EOFError, gzip.BadGzipFile):
        raise ManifestError("chunk_unreadable") from None
    if sha256_bytes(payload) != entry.get("sha256"):
        raise ManifestError("chunk_altered")
    rows = []
    for line in payload.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            raise ManifestError("chunk_row") from None
        if not isinstance(row, list) or len(row) != width:
            raise ManifestError("chunk_row")
        rows.append(row)
    if len(rows) != entry.get("rows"):
        raise ManifestError("chunk_rows")
    return rows


# ── Export manifest ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExportManifest:
    path: Path
    sha256: str
    document: dict[str, Any]

    @property
    def directory(self) -> Path:
        return self.path.parent

    @property
    def snapshot_id(self) -> UUID:
        return UUID(self.document["snapshot_id"])

    @property
    def tables(self) -> dict[str, dict[str, Any]]:
        return self.document["tables"]

    def chunks(self, table: str) -> Iterator[tuple[int, dict[str, Any]]]:
        yield from enumerate(self.tables[table].get("chunks", []))


def load_export_manifest(path: Path, expected_sha256: str | None = None) -> ExportManifest:
    try:
        data = path.read_bytes()
    except OSError:
        raise ManifestError("export_manifest_unreadable") from None
    if len(data) > _MAX_MANIFEST_BYTES:
        raise ManifestError("export_manifest_size")
    digest = sha256_bytes(data)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ManifestError("export_manifest_digest")
    try:
        document = json.loads(data)
    except ValueError:
        raise ManifestError("export_manifest_json") from None
    if (
        not isinstance(document, dict)
        or document.get("format") != EXPORT_FORMAT
        or not isinstance(document.get("tables"), dict)
    ):
        raise ManifestError("export_manifest_format")
    try:
        if UUID(document["snapshot_id"]).int == 0:
            raise ManifestError("export_snapshot")
    except (KeyError, TypeError, ValueError):
        raise ManifestError("export_snapshot") from None
    if data != canonical_json(document):
        raise ManifestError("export_manifest_canonical")
    return ExportManifest(path, digest, document)


# ── Validated manifest: the epoch-publication contract ─────────────────────


def migration_ledger_sha256(rows: Iterable[tuple[int, str, str]]) -> str:
    """Digest of ``schema_migrations`` (version, name, checksum) in version order."""
    lines = [f"{int(v)}:{n}:{c}\n" for v, n, c in sorted(rows, key=lambda row: int(row[0]))]
    return sha256_bytes("".join(lines).encode())


class ValidatedManifestRefused(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Validated import manifest refused: {reason}")


def verify_validated_manifest(
    path: Path,
    expected_sha256: str,
    *,
    schema_version: int,
    ledger_rows: Iterable[tuple[int, str, str]],
) -> dict[str, Any]:
    """Return the manifest only if it authorizes publishing this schema's epoch.

    Epoch publication calls this with the operator-supplied digest and the live
    ``schema_migrations`` rows, inside its maintenance transaction.
    """
    try:
        with path.open("rb") as handle:
            data = handle.read(_MAX_MANIFEST_BYTES + 1)
    except OSError:
        raise ValidatedManifestRefused("unreadable") from None
    return verify_validated_document(
        data,
        expected_sha256,
        schema_version=schema_version,
        ledger_rows=ledger_rows,
    )


def verify_validated_document(
    data: bytes,
    expected_sha256: str,
    *,
    schema_version: int,
    ledger_rows: Iterable[tuple[int, str, str]],
) -> dict[str, Any]:
    """Validate the exact bytes the cutover journal will bind to its decision."""
    if not isinstance(expected_sha256, str) or not _HEX64.fullmatch(expected_sha256):
        raise ValidatedManifestRefused("digest_format")
    if len(data) > _MAX_MANIFEST_BYTES or sha256_bytes(data) != expected_sha256:
        raise ValidatedManifestRefused("digest_mismatch")
    try:
        document = json.loads(data)
    except ValueError:
        raise ValidatedManifestRefused("json") from None
    if not isinstance(document, dict) or data != canonical_json(document):
        raise ValidatedManifestRefused("canonical")
    if document.get("format") != VALIDATION_FORMAT or document.get("status") != VALIDATED:
        raise ValidatedManifestRefused("status")
    if (
        type(document.get("schema_version")) is not int
        or document["schema_version"] != schema_version
    ):
        raise ValidatedManifestRefused("schema_version")
    if document.get("migration_ledger_sha256") != migration_ledger_sha256(ledger_rows):
        raise ValidatedManifestRefused("migration_ledger")
    conflicts = document.get("conflicts")
    if (
        not isinstance(conflicts, dict)
        or type(conflicts.get("blocking")) is not int
        or conflicts["blocking"] != 0
    ):
        raise ValidatedManifestRefused("blocking_conflicts")
    tables = document.get("tables")
    if (
        not isinstance(tables, dict)
        or not tables
        or any(
            not isinstance(entry, dict) or entry.get("status") not in ("MATCHED", "ABSENT")
            for entry in tables.values()
        )
    ):
        raise ValidatedManifestRefused("tables")
    consistency = document.get("source_consistency")
    if consistency != "FROZEN_STABLE":
        raise ValidatedManifestRefused("source_consistency")
    for field in ("snapshot_id", "import_run_id"):
        try:
            if UUID(document[field]).int == 0:
                raise ValidatedManifestRefused(field)
        except (KeyError, TypeError, ValueError):
            raise ValidatedManifestRefused(field) from None
    if not _HEX64.fullmatch(str(document.get("export_manifest_sha256", ""))):
        raise ValidatedManifestRefused("export_manifest_sha256")
    return document
