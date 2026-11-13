import pytest

from app.core import metadata_runtime
from app.core.metadata_db import MetadataUnavailable


class _Database:
    pass


def test_metadata_db_refuses_when_nothing_is_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metadata_runtime, "_database", None)
    with pytest.raises(MetadataUnavailable):
        metadata_runtime.metadata_db()


def test_install_returns_the_same_database_and_refuses_a_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(metadata_runtime, "_database", None)
    first, second = _Database(), _Database()
    metadata_runtime.install_metadata_db(first)  # type: ignore[arg-type]
    metadata_runtime.install_metadata_db(first)  # type: ignore[arg-type]
    assert metadata_runtime.metadata_db() is first
    with pytest.raises(RuntimeError):
        metadata_runtime.install_metadata_db(second)  # type: ignore[arg-type]
    metadata_runtime.uninstall_metadata_db(second)  # type: ignore[arg-type]
    assert metadata_runtime.metadata_db() is first
    metadata_runtime.uninstall_metadata_db(first)  # type: ignore[arg-type]
    with pytest.raises(MetadataUnavailable):
        metadata_runtime.metadata_db()
