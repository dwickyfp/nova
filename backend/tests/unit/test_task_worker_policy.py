import pytest

from app.core.config import settings
from scripts.provision_task_worker_access import scoped_policy


def test_worker_policy_only_targets_explicit_execution_identity(monkeypatch):
    monkeypatch.setattr(settings, "WORKER_IMPERSONATION_USER", "nova_task_worker")
    monkeypatch.setattr(settings, "WORKER_IMPERSONATION_ROLE", "nova_task_worker_role")
    policy = scoped_policy("dwicky.f.putra")
    assert policy.resources["user"].values == ["dwicky.f.putra"]
    assert not policy.resources["user"].is_excludes
    item = policy.policy_items[0]
    assert item.roles == ["nova_task_worker_role"]
    assert [access.type for access in item.accesses] == ["impersonate"]
    assert not item.delegate_admin


@pytest.mark.parametrize("owner", ["root", "*", "nova_*", "x'@'%'", ""])
def test_worker_policy_rejects_broad_or_injected_identity(owner):
    with pytest.raises(ValueError):
        scoped_policy(owner)
