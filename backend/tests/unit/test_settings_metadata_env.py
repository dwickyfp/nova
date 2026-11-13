"""A fresh process can read application and PG settings from the same dev file."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import MetadataRuntimeSettings, Settings


@pytest.fixture
def shared_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in os.environ:
        if key.upper().startswith("NOVA_METADATA_") or key == "LOG_LEVEL":
            monkeypatch.delenv(key)
    path = tmp_path / ".env"
    path.write_text(
        "LOG_LEVEL=DEBUG\n"
        "NOVA_METADATA_HOST=localhost\n"
        "NOVA_METADATA_PORT=55432\n"
        "NOVA_METADATA_SSLROOTCERT=/private/fixture.crt\n"
        "NOVA_METADATA_SCHEMA_VERSION=44\n"
        "NOVA_METADATA_EPOCH=11111111-1111-4111-8111-111111111111\n"
        "NOVA_METADATA_PASSWORDS_FILE=/private/workloads.json\n"
        "NOVA_METADATA_POOL_MAX_SIZE=3\n"
    )
    return path


def test_fresh_import_loads_both_settings_owners(shared_env: Path) -> None:
    backend = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", (
            "import json; from app.core.config import settings, MetadataRuntimeSettings; "
            "pg = MetadataRuntimeSettings(); print(json.dumps([settings.LOG_LEVEL, "
            "pg.host, pg.port, pg.pool_max_size]))"
        )],
        cwd=shared_env.parent, env={**os.environ, "PYTHONPATH": str(backend)},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["DEBUG", "localhost", 55432, 3]


def test_metadata_credentials_do_not_enter_application_settings(shared_env: Path) -> None:
    with shared_env.open("a") as handle:
        handle.write('NOVA_METADATA_PASSWORDS=\'{"nova_meta_web":"fixture-secret-canary"}\'\n')
    app = Settings(_env_file=shared_env)
    pg = MetadataRuntimeSettings(_env_file=shared_env)
    assert "fixture-secret-canary" not in app.model_dump_json()
    assert pg.passwords and "fixture-secret-canary" in pg.passwords.get_secret_value()


@pytest.mark.parametrize("key", [
    "NOVA_METADATA_HOST_TYPO", "NOVA_METADATA_MIGRATOR_DSN", "UNRECOGNIZED_SETTING",
])
def test_unowned_file_fields_are_still_rejected_without_echoing_values(
    shared_env: Path, key: str,
) -> None:
    with shared_env.open("a") as handle:
        handle.write(f"{key}=private-config-canary\n")
    with pytest.raises(ValidationError) as failure:
        Settings(_env_file=shared_env)
    assert any(error["type"] == "extra_forbidden" for error in failure.value.errors())
    assert "private-config-canary" not in str(failure.value)


def test_metadata_owner_still_validates_its_values(shared_env: Path) -> None:
    shared_env.write_text(shared_env.read_text().replace("PORT=55432", "PORT=invalid-port"))
    assert Settings(_env_file=shared_env).LOG_LEVEL == "DEBUG"
    with pytest.raises(ValidationError) as failure:
        MetadataRuntimeSettings(_env_file=shared_env)
    assert "invalid-port" not in str(failure.value)


def test_process_environment_keeps_precedence(
    shared_env: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("NOVA_METADATA_PORT", "55433")
    assert Settings(_env_file=shared_env).LOG_LEVEL == "WARNING"
    assert MetadataRuntimeSettings(_env_file=shared_env).port == 55433
