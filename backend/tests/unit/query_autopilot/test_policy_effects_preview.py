from copy import deepcopy

import pytest

from app.core.config import settings
from app.modules.access_control.service import AccessControlService


@pytest.mark.parametrize(
    "change,supported",
    [
        ({}, True),
        ({"users": ["alice"]}, False),
        ({"groups": ["finance-users"]}, False),
        ({"conditions": [{"type": "ip-range", "values": ["127.0.0.1"]}]}, False),
    ],
)
def test_role_only_preview_cannot_prove_principal_or_conditional_policy_parity(change, supported):
    policy = {
        "service": settings.RANGER_SERVICE_NAME,
        "policyType": 1,
        "resources": {"database": {"values": ["retail"]}, "table": {"values": ["orders"]}},
        "rowFilterPolicyItems": [{"roles": ["finance"], **change}],
    }
    assert (
        AccessControlService._security_effects_comparison_supported(
            [policy],
            "default_catalog",
            "retail",
            "orders",
        )
        is supported
    )
    other = deepcopy(policy)
    other["resources"]["database"]["values"] = ["unrelated"]
    assert (
        AccessControlService._security_effects_comparison_supported(
            [other],
            "default_catalog",
            "retail",
            "orders",
        )
        is True
    )


@pytest.mark.parametrize(
    "resource",
    [
        {"values": ["retail"], "isExcludes": True},
        {"values": ["retail"], "isRecursive": True},
        {"values": ["retail*"]},
        {"values": ["retail?"]},
        {"values": ["retail[12]"]},
    ],
)
def test_complex_resource_predicates_are_not_inferred_by_the_metadata_preview(resource):
    policy = {
        "service": settings.RANGER_SERVICE_NAME,
        "policyType": 2,
        "resources": {"database": resource},
        "dataMaskPolicyItems": [{"roles": ["finance"]}],
    }
    assert (
        AccessControlService._security_effects_comparison_supported(
            [policy],
            "default_catalog",
            "retail",
            "orders",
        )
        is False
    )


def test_complete_policy_projection_covers_other_roles_custom_masks_and_priority():
    policy = {
        "service": settings.RANGER_SERVICE_NAME,
        "policyType": 2,
        "resources": {
            "database": {"values": ["retail"]},
            "table": {"values": ["orders"]},
            "column": {"values": ["phone"]},
        },
        "dataMaskPolicyItems": [
            {
                "roles": ["parent_role"],
                "dataMaskInfo": {"dataMaskType": "CUSTOM", "valueExpr": "'a'"},
            }
        ],
    }
    initial = AccessControlService._security_effects_fingerprint(
        [policy],
        "default_catalog",
        "retail",
        "orders",
    )
    copied = deepcopy(policy)
    copied["resources"]["database"]["values"] = ["snapshot"]
    copied["id"], copied["name"] = 42, "different-policy-identity"
    assert (
        AccessControlService._security_effects_fingerprint(
            [copied],
            "default_catalog",
            "snapshot",
            "orders",
        )
        == initial
    )
    for changed in ("expression", "role", "priority"):
        altered = deepcopy(policy)
        if changed == "expression":
            altered["dataMaskPolicyItems"][0]["dataMaskInfo"]["valueExpr"] = "'b'"
        elif changed == "role":
            altered["dataMaskPolicyItems"][0]["roles"] = ["another_parent"]
        else:
            altered["policyPriority"] = 1
        assert (
            AccessControlService._security_effects_fingerprint(
                [altered],
                "default_catalog",
                "retail",
                "orders",
            )
            != initial
        )
