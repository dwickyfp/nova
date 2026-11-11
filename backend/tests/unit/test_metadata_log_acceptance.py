import json

import pytest

from tests.integration.check_metadata_logs import scan


@pytest.mark.parametrize("value", [
    "synthetic-secret-canary", "synthetic-registry-secret", "synthetic-import-canary",
    "synthetic-plaintext-secret-canary", "secret-canary", "private-header-canary",
    "parameter-secret-canary", "gAAAA" + "a" * 80,
    "synthetic-parameter-key", "synthetic-header",
])
def test_gate_detects_values_inside_primary_error_messages_without_reporting_them(value):
    result = scan(f'ERROR: invalid input syntax for type integer: "{value}"\n')
    assert not result["passed"]
    assert sum(result["matches"].values()) >= 1
    assert value not in json.dumps(result)


def test_empty_capture_is_not_a_passing_scan():
    assert not scan("")["passed"]
    assert scan("LOG: database system is ready to accept connections\n")["passed"]
