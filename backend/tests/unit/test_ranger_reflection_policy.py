"""Every user may read table reflection; the bootstrap and the compiler agree."""

import ast
from pathlib import Path

from app.integrations.ranger.compiler import BI_METADATA_TABLES, compile_reflection_policy

BOOTSTRAP = Path(__file__).resolve().parents[3] / "docker" / "ranger" / "bootstrap.py"


def _bootstrap_policy(name: str) -> dict:
    tree = ast.parse(BOOTSTRAP.read_text())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "put_policy"
                and node.args and isinstance(node.args[0], ast.Dict)):
            payload = ast.literal_eval(node.args[0])
            if payload.get("name") == name:
                return payload
    raise AssertionError(f"{name} is not provisioned by the Ranger bootstrap")


def test_reflection_is_granted_to_everyone_and_adds_no_object_access():
    policy = compile_reflection_policy()
    api = policy.to_api()
    assert api["resources"]["database"]["values"] == ["information_schema"]
    assert api["resources"]["table"]["values"] == list(BI_METADATA_TABLES)
    (item,) = api["policyItems"]
    assert item["groups"] == ["public"] and [a["type"] for a in item["accesses"]] == ["select"]


def test_the_bootstrap_provisions_the_same_reflection_policy():
    compiled = compile_reflection_policy().to_api()
    seeded = _bootstrap_policy(compiled["name"])
    assert seeded["resources"]["database"]["values"] == ["information_schema"]
    assert seeded["resources"]["table"]["values"] == compiled["resources"]["table"]["values"]
    assert seeded["resources"]["column"]["values"] == ["*"]
    (item,) = seeded["policyItems"]
    assert item["groups"] == ["public"]
    assert [a["type"] for a in item["accesses"]] == ["select"]
