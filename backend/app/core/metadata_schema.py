"""Immutable, ordered PostgreSQL schema revisions shipped with this binary."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MIGRATION_DIRECTORY = Path(__file__).resolve().parents[2] / "metadata_migrations"
_NAME = re.compile(r"([0-9]{4})_[a-z][a-z0-9_]*\.sql")


class SchemaManifestError(ValueError):
    def __init__(self) -> None:
        super().__init__("Metadata migration manifest is missing, invalid or inconsistent")


@dataclass(frozen=True, slots=True)
class SchemaMigration:
    version: int
    name: str
    checksum: str
    statement: str = field(repr=False)


def load_migrations(directory: Path = MIGRATION_DIRECTORY) -> tuple[SchemaMigration, ...]:
    migrations: list[SchemaMigration] = []
    try:
        for path in sorted(directory.glob("*.sql")):
            match = _NAME.fullmatch(path.name)
            if not match or path.is_symlink() or not 0 < path.stat().st_size <= 4 * 1024 * 1024:
                raise SchemaManifestError()
            version = int(match[1])
            if version != len(migrations) + 1:
                raise SchemaManifestError()
            content = path.read_bytes()
            statement = content.decode("utf-8")
            if (
                not statement.strip() or "\x00" in statement
                or "$nova_schema_revision$" in statement
            ):
                raise SchemaManifestError()
            migrations.append(SchemaMigration(
                version, path.name, hashlib.sha256(content).hexdigest(), statement,
            ))
    except (OSError, UnicodeError):
        raise SchemaManifestError() from None
    if not migrations:
        raise SchemaManifestError()
    return tuple(migrations)


def verify_applied_migrations(
    migrations: Sequence[SchemaMigration], applied: Sequence[Mapping[str, Any]],
) -> int:
    """An existing ledger must be an exact prefix, never a divergent history."""
    if len(applied) > len(migrations):
        raise SchemaManifestError()
    for expected, actual in zip(migrations, applied, strict=False):
        if (
            actual.get("version") != expected.version
            or actual.get("name") != expected.name
            or actual.get("checksum") != expected.checksum
        ):
            raise SchemaManifestError()
    return len(applied)
