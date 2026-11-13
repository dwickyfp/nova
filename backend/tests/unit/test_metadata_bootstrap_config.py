"""Bootstrap inputs never expose credentials or silently accept incomplete role maps."""

import json
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import MetadataBootstrapSettings
from app.core.metadata_bootstrap import (
    LOGIN_ROLES,
    BootstrapConfigurationError,
    BootstrapCredentials,
)


def passwords():
    return {role: f"synthetic-only-password-{index:04d}" for index, role in enumerate(LOGIN_ROLES)}


def test_credentials_are_immutable_and_redacted():
    credentials = BootstrapCredentials.from_json(SecretStr(json.dumps(passwords())))
    assert "synthetic-only" not in repr(credentials)
    with pytest.raises(TypeError):
        credentials.passwords[LOGIN_ROLES[0]] = SecretStr("replacement")


@pytest.mark.parametrize("fault", ["missing", "extra", "shared", "short", "null", "placeholder"])
def test_invalid_password_map_is_refused_without_secret_readback(fault):
    values = passwords()
    if fault == "missing":
        values.pop(LOGIN_ROLES[0])
    elif fault == "extra":
        values["unexpected"] = "synthetic-only-canary-password"
    elif fault == "shared":
        values[LOGIN_ROLES[0]] = values[LOGIN_ROLES[1]]
    elif fault == "short":
        values[LOGIN_ROLES[0]] = "short-canary"
    elif fault == "null":
        values[LOGIN_ROLES[0]] = None
    else:
        values[LOGIN_ROLES[0]] = "CHANGE_ME_to_a_unique_secret_password"
    with pytest.raises(BootstrapConfigurationError) as caught:
        BootstrapCredentials.from_json(SecretStr(json.dumps(values)))
    assert "canary" not in str(caught.value) and "synthetic" not in str(caught.value)


@pytest.mark.parametrize("raw", [
    '{"canary":"secret","canary":"replacement"}', "[]", "null", "not-json-canary",
    '{"canary":' + '"' + "x" * 16384 + '"}',
])
def test_malformed_oversized_or_duplicate_json_is_refused(raw):
    with pytest.raises(BootstrapConfigurationError) as caught:
        BootstrapCredentials.from_json(SecretStr(raw))
    assert raw not in str(caught.value)


def test_bootstrap_settings_do_not_load_dotenv_and_hide_inputs(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in ("DSN", "DEPLOYMENT_ID", "ROLE_PASSWORDS"):
        monkeypatch.delenv("NOVA_METADATA_BOOTSTRAP_" + key, raising=False)
    (tmp_path / ".env").write_text("NOVA_METADATA_BOOTSTRAP_DSN=dotenv-secret-canary\n")
    with pytest.raises(ValidationError) as caught:
        MetadataBootstrapSettings()
    assert "dotenv-secret-canary" not in str(caught.value)
    value = MetadataBootstrapSettings(
        dsn="explicit-secret-canary", deployment_id=uuid4(), role_passwords="map-secret-canary",
    )
    assert "secret-canary" not in repr(value)
