"""Only the active immutable admin role can manage another owner's metadata."""

from app.modules.intelligence.access import can_manage


def test_intelligence_management_uses_active_role():
    assert can_manage("alice", {"username": "alice", "active_role": "ANALYST"})
    assert can_manage("alice", {"username": "nova_admin", "active_role": "ACCOUNTADMIN"})
    assert not can_manage("alice", {
        "username": "bob", "active_role": "ANALYST", "assigned_roles": ["ACCOUNTADMIN"],
    })
