"""Static assertions over ``docker/init-nova.sql``'s ACCOUNTADMIN grants.

The super-user role is easy to get subtly wrong, and the failure is invisible
until a user hits it: `ALL ON CATALOG` does **not** include `CREATE DATABASE`,
so a role holding every other catalog privilege is still refused `CREATE
DATABASE` with error 5203. That exact gap shipped and was found by a user, not
by CI, because the integration seed grants `nova_admin` `ALL ON *.*` directly
and never exercises the role.

This test parses the script as text — no engine, no network — so the regression
is caught in the fast local loop. It asserts the grants that are load-bearing
for "ACCOUNTADMIN = maximum StarRocks privilege", not the full script, so an
unrelated line can be added without breaking it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

#: Repo root → docker/init-nova.sql. The test file lives at
#: backend/tests/unit/<this file>, so the root is three parents up.
INIT_SQL = Path(__file__).resolve().parents[3] / "docker" / "init-nova.sql"


def _read_init_sql() -> str:
    if not INIT_SQL.is_file():
        pytest.skip(f"init script not found at {INIT_SQL}")
    return INIT_SQL.read_text()


def _grant_statements(sql: str) -> str:
    """Only the ``GRANT ... TO ROLE ACCOUNTADMIN`` statements, comment-stripped.

    Comments are removed so a grant merely *described* in a comment cannot
    satisfy an assertion.
    """
    without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    without_line = re.sub(r"--[^\n]*", " ", without_block)
    statements = [s.strip() for s in without_line.split(";")]
    return "\n".join(
        s.upper() for s in statements if re.search(r"TO\s+ROLE\s+ACCOUNTADMIN", s, re.I)
    )


@pytest.fixture(scope="module")
def grants() -> str:
    return _grant_statements(_read_init_sql())


#: The built-in roles. A custom role cannot hold `NODE` and the other
#: cluster-admin bits directly — the engine directs callers to a built-in role —
#: so granting these is what makes ACCOUNTADMIN equal to `root`.
BUILTIN_ROLES = [
    "root",
    "db_admin",
    "cluster_admin",
    "user_admin",
    "security_admin",
]


@pytest.mark.parametrize("role", BUILTIN_ROLES)
def test_every_builtin_role_is_granted(grants: str, role: str) -> None:
    assert re.search(rf"GRANT\s+{role.upper()}\s+TO\s+ROLE\s+ACCOUNTADMIN", grants), (
        f"built-in role '{role}' is not granted to ACCOUNTADMIN"
    )


def test_create_database_on_catalog_is_granted(grants: str) -> None:
    # The regression: ALL ON CATALOG omits CREATE DATABASE.
    assert re.search(
        r"GRANT\s+CREATE\s+DATABASE\s+ON\s+CATALOG\s+DEFAULT_CATALOG\s+TO\s+ROLE\s+ACCOUNTADMIN",
        grants,
    ), "CREATE DATABASE ON CATALOG default_catalog is missing"


def test_all_on_catalog_is_granted(grants: str) -> None:
    assert re.search(r"GRANT\s+ALL\s+ON\s+CATALOG\s+DEFAULT_CATALOG", grants)


@pytest.mark.parametrize(
    "privilege",
    [
        "OPERATE",
        "CREATE RESOURCE GROUP",
        "CREATE WAREHOUSE",
        "CREATE RESOURCE",
        "CREATE EXTERNAL CATALOG",
        "REPOSITORY",
        "CREATE STORAGE VOLUME",
        "BLACKLIST",
        "FILE",
        "PLUGIN",
        "SECURITY",
    ],
)
def test_system_level_privileges_are_granted(grants: str, privilege: str) -> None:
    assert re.search(
        rf"GRANT\b[^;\n]*\b{privilege}\s+ON\s+SYSTEM\b", grants
    ), f"system privilege '{privilege}' is not granted on SYSTEM"


@pytest.mark.parametrize("privilege", ["APPLY", "CREATE CNGROUP"])
def test_ungrantable_privileges_are_absent(grants: str, privilege: str) -> None:
    # The 4.1.x engine rejects these with "cannot find privilege type". Listing
    # them would make the init script fail partway and leave the role built
    # inconsistently. `IMPERSONATE` is excluded by design (not grantable on
    # SYSTEM), and `NODE` is held through `root`.
    assert not re.search(
        rf"GRANT\b[^;\n]*\b{privilege}\b", grants
    ), f"'{privilege}' cannot be granted on 4.1.x but appears in the script"
