"""Security tests for the internal Ranger policy-download bridge."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "docker" / "ranger" / "policy_proxy.py"


def _load_module():
    os.environ.setdefault("RANGER_PASSWORD", "unit-test-only")
    spec = importlib.util.spec_from_file_location("ranger_policy_proxy", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


proxy = _load_module()


def test_public_policy_download_maps_to_authenticated_endpoint() -> None:
    path = "/service/plugins/policies/download/nova_starrocks?lastKnownVersion=-1"
    assert proxy.is_allowed_path(path)
    assert proxy.secure_path(path) == (
        "/service/plugins/secure/policies/download/nova_starrocks"
        "?lastKnownVersion=-1"
    )


def test_all_plugin_downloads_have_secure_mappings() -> None:
    paths = {
        "/service/roles/download/nova_starrocks": (
            "/service/roles/secure/download/nova_starrocks"
        ),
        "/service/tags/download/nova_starrocks": (
            "/service/tags/secure/download/nova_starrocks"
        ),
        "/service/xusers/download/nova_starrocks": (
            "/service/xusers/secure/download/nova_starrocks"
        ),
    }
    for public_path, expected in paths.items():
        assert proxy.is_allowed_path(public_path)
        assert proxy.secure_path(public_path) == expected


def test_proxy_rejects_non_download_and_absolute_urls() -> None:
    rejected = (
        "/service/public/v2/api/policy",
        "/service/plugins/secure/policies/download/nova_starrocks",
        "http://ranger-admin:6080/service/plugins/policies/download/nova_starrocks",
        "//ranger-admin:6080/service/plugins/policies/download/nova_starrocks",
    )
    for path in rejected:
        assert not proxy.is_allowed_path(path)

