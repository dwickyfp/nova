import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("changed_layout", [False, True])
def test_ranger_adapter_separates_admin_password_and_keeps_nova_service_owner(
    tmp_path, changed_layout
):
    upstream = tmp_path / "ranger.sh"
    upstream.write_text(
        "#!/bin/bash\n"
        + ('echo "rangerAdmin_password=${RANGER_DB_PASSWORD}"\n' if not changed_layout else "")
        + "python3 ${RANGER_SCRIPTS}/create-ranger-services.py\n"
    )
    (tmp_path / "create-ranger-services.py").write_text(
        'raise SystemExit("Vendor demo setup must not run")'
    )
    result = subprocess.run(
        ["bash", str(ROOT / "docker/ranger/admin-start.sh")],
        env={
            **os.environ,
            "RANGER_SCRIPTS": str(tmp_path),
            "RANGER_DB_PASSWORD": "database-only",
            "RANGER_ADMIN_PASSWORD": "admin-only",
        },
        capture_output=True,
        text=True,
    )
    if changed_layout:
        assert result.returncode != 0
        assert "review the pinned image adapter" in result.stderr
    else:
        assert result.returncode == 0
        assert result.stdout.strip() == "rangerAdmin_password=admin-only"
    assert "database-only" not in result.stdout + result.stderr


def test_compose_admin_clients_and_authenticated_readiness_use_admin_secret():
    config = yaml.safe_load((ROOT / "docker/docker-compose-engine.yml").read_text())
    services = config["services"]
    for name in ["nova-backend", "nova-worker", "ranger-bootstrap", "ranger-policy-proxy"]:
        value = services[name]["environment"]["RANGER_PASSWORD"]
        assert "RANGER_ADMIN_PASSWORD" in value
        assert "RANGER_DB_PASSWORD" not in value
    readiness = services["ranger-admin"]["healthcheck"]["test"][-1]
    assert "/service/public/v2/api/service" in readiness
    assert "$${RANGER_ADMIN_PASSWORD}" in readiness
    assert services["ranger-admin"]["entrypoint"] == [
        "/bin/bash",
        "/opt/nova/ranger-admin-start.sh",
    ]
