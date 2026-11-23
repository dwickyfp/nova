"""role_allows never allows what Ranger would refuse (Stream capture acts on it)."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.modules.access_control.domain import AccessRequest
from app.modules.access_control.service import AccessControlService
from app.modules.streams.sources import journal_guard_policy
from tests.unit.stream_metadata_fakes import STARROCKS_ACCESS_TYPES

INSERT = AccessRequest("insert", "default_catalog", "sales", "orders")
CREATE_TABLE = AccessRequest("create table", "default_catalog", "sales")


def _policy(database="sales", table="*", *, role="ANALYST", access="insert", **extra):
    resources = {
        "catalog": {"values": ["default_catalog"]},
        "database": {"values": [database]},
    }
    if table is not None:
        resources |= {"table": {"values": [table]}, "column": {"values": ["*"]}}
    return {
        "service": settings.RANGER_SERVICE_NAME, "isEnabled": True, "policyType": 0,
        "resources": resources,
        "policyItems": [{"roles": [role], "accesses": [{"type": access, "isAllowed": True}]}],
        **extra,
    }


def _allows(policies, request=INSERT, user="alice", role="ANALYST") -> bool:
    return AccessControlService._policies_allow(policies, user, role, request)


def test_plain_allow_still_allows():
    assert _allows([_policy()])
    assert _allows([_policy(table="orders")])


def test_allow_exception_refuses():
    policy = _policy(allowExceptions=[
        {"users": ["bob"], "accesses": [{"type": "insert", "isAllowed": True}]}
    ])
    assert not _allows([policy], user="bob")
    assert not _allows([policy], user="alice")  # not evaluated: fail closed for everyone


def test_deny_all_else_refuses():
    owner_only = _policy(table="orders", role="OWNER", isDenyAllElse=True)
    assert not _allows([owner_only, _policy()])


@pytest.mark.parametrize("priority", [1, "1"])
def test_override_priority_refuses(priority):
    assert not _allows([_policy(policyPriority=priority)])


@pytest.mark.parametrize("table", ["ord*", "ord?rs", "o[r]ders"])
def test_wildcard_patterns_refuse(table):
    assert not _allows([_policy(table=table)])


def test_exclusions_refuse_when_they_could_cover():
    policy = _policy(table="salaries")
    policy["resources"]["table"]["isExcludes"] = True
    assert not _allows([policy])


def test_existing_denies_still_refuse():
    deny = _policy(denyPolicyItems=[
        {"roles": ["ANALYST"], "accesses": [{"type": "insert", "isAllowed": True}]}
    ])
    assert not _allows([deny, _policy()])


def test_unevaluated_policies_elsewhere_do_not_refuse():
    hr = _policy(database="hr", allowExceptions=[
        {"users": ["alice"], "accesses": [{"type": "insert", "isAllowed": True}]}
    ])
    excluded = _policy(table="orders")
    excluded["resources"]["table"]["isExcludes"] = True  # covers everything but orders
    assert _allows([hr, excluded, _policy()])


def test_stream_journal_guard_does_not_refuse_ordinary_tables(monkeypatch):
    monkeypatch.setattr(settings, "STREAM_WRITER_ROLE", "nova_stream_writer_role")
    guard = journal_guard_policy(STARROCKS_ACCESS_TYPES).to_api()
    assert _allows([guard, _policy()])
    journal = AccessRequest("insert", "default_catalog", "sales", "__nova_stream_changes_x")
    assert not _allows([guard, _policy()], request=journal)


def test_database_requests_ignore_table_policies():
    table_glob = _policy(table="ord*")
    database = _policy(table=None, access="create table")
    assert _allows([table_glob, database], request=CREATE_TABLE)
    database_glob = _policy(database="sal*", table=None, access="create table")
    assert not _allows([database_glob], request=CREATE_TABLE)
