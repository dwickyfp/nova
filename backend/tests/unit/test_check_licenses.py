"""NDV-OSS-001/002: the dependency license gate refuses what it must."""

import importlib.util
from email.message import Message
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_licenses", Path(__file__).resolve().parents[2] / "scripts" / "check_licenses.py")
assert _SPEC and _SPEC.loader
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)


def metadata(**fields) -> Message:
    message = Message()
    for key, value in fields.items():
        for item in value if isinstance(value, list) else [value]:
            message[key.replace("_", "-")] = item
    return message


@pytest.mark.parametrize("expression, package, expected", [
    ("MIT", "any", True),
    ("MIT OR GPL-3.0-only", "any", True),
    ("MIT AND GPL-3.0-only", "any", False),
    ("(MPL-2.0 OR Apache-2.0)", "any", True),
    ("BSD-2-Clause AND Apache-2.0 WITH LLVM-exception", "any", True),
    ("Apache-2.0 WITH Classpath-exception-2.0", "any", False),
    ("BUSL-1.1", "any", False),
    ("SSPL-1.0", "any", False),
    ("Elastic-2.0", "any", False),
    ("AGPL-3.0-only", "any", False),
    ("LGPL-3.0-only", "psycopg", True),
    ("LGPL-3.0-only", "another-package", False),
    ("(MIT", "any", False),
])
def test_spdx_expressions_are_evaluated_against_the_allowlist(expression, package, expected):
    assert gate.accepted(expression, package) is expected


def test_license_metadata_is_read_from_expression_field_text_or_classifiers():
    assert gate.declared(metadata(License_Expression="Apache-2.0")) == "Apache-2.0"
    assert gate.declared(metadata(License="MPL-2.0 AND MIT")) == "MPL-2.0 AND MIT"
    assert gate.declared(metadata(License="MIT License\n\nCopyright ...")) == "MIT"
    assert gate.declared(metadata(
        License="Copyright (c) 2001, Example", Classifier=[
            "License :: OSI Approved :: BSD License"])) == "BSD-3-Clause"
    # A license that cannot be identified is a finding, never a pass.
    assert gate.declared(metadata(License="See LICENSE file")) is None
    assert gate.declared(metadata(Classifier=["License :: Other/Proprietary License"])) is None


def test_commercial_packages_and_license_key_calls_are_recognised():
    assert gate.COMMERCIAL_PACKAGES.match("highcharts-core")
    assert gate.COMMERCIAL_PACKAGES.match("streamlit-aggrid")
    assert not gate.COMMERCIAL_PACKAGES.match("echarts")
    assert gate.LICENSE_KEY_API.search("LicenseManager.setLicenseKey(KEY)")
    assert gate.LICENSE_KEY_API.search("license_key = settings.KEY")
    assert not gate.LICENSE_KEY_API.search("licenses = audit()")
