from pathlib import Path

import pytest

from app.core.metadata_schema import (
    SchemaManifestError,
    load_migrations,
    verify_applied_migrations,
)


@pytest.mark.parametrize("files", [
    {}, {"0002_gap.sql": "SELECT 1;"}, {"0000_zero.sql": "SELECT 1;"},
    {"revision.sql": "SELECT 1;"}, {"0001_empty.sql": " "},
    {"0001_first.sql": "SELECT 1;", "0001_duplicate.sql": "SELECT 2;"},
    {"0001_first.sql": "$nova_schema_revision$"},
])
def test_invalid_manifest_refused_before_connecting(tmp_path, files):
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    with pytest.raises(SchemaManifestError):
        load_migrations(tmp_path)


def test_manifest_rejects_symlink_and_invalid_encoding(tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("SELECT 'synthetic-secret-canary';")
    path = tmp_path / "0001_first.sql"
    path.symlink_to(outside)
    with pytest.raises(SchemaManifestError):
        load_migrations(tmp_path)
    path.unlink()
    path.write_bytes(b"\xff")
    with pytest.raises(SchemaManifestError):
        load_migrations(tmp_path)


def test_ledger_requires_exact_order_name_and_bytes(tmp_path):
    path: Path = tmp_path / "0001_first.sql"
    path.write_text("SELECT 'synthetic-secret-canary';")
    manifest = load_migrations(tmp_path)
    migration = manifest[0]
    assert "synthetic-secret-canary" not in repr(migration)
    row = {"version": 1, "name": path.name, "checksum": migration.checksum}
    assert verify_applied_migrations(manifest, [row]) == 1
    for altered in [{**row, "version": 2}, {**row, "name": "0001_renamed.sql"}]:
        with pytest.raises(SchemaManifestError):
            verify_applied_migrations(manifest, [altered])
    with pytest.raises(SchemaManifestError):
        verify_applied_migrations(manifest, [row, row])
    path.write_text(path.read_text() + "\n")
    with pytest.raises(SchemaManifestError):
        verify_applied_migrations(load_migrations(tmp_path), [row])
