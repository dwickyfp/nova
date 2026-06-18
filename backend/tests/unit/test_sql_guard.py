"""Unit tests for SQL guard — no engine needed."""

import pytest

from app.common.sql_guard import guard_sql
from app.core.exceptions import ForbiddenSQLError


class TestSqlGuard:
    def test_safe_select_passes(self):
        guard_sql("SELECT * FROM my_table")

    def test_safe_create_table_passes(self):
        guard_sql("CREATE TABLE test (id INT)")

    def test_drop_role_accountadmin_blocked(self):
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql("DROP ROLE ACCOUNTADMIN")

    def test_drop_role_accountadmin_case_insensitive(self):
        with pytest.raises(ForbiddenSQLError):
            guard_sql("drop role accountadmin")

    def test_revoke_from_accountadmin_blocked(self):
        with pytest.raises(ForbiddenSQLError, match="revoke"):
            guard_sql("REVOKE ALL ON *.* FROM ROLE ACCOUNTADMIN")

    def test_alter_role_accountadmin_blocked(self):
        with pytest.raises(ForbiddenSQLError):
            guard_sql("ALTER ROLE ACCOUNTADMIN RENAME TO admin")

    def test_drop_user_root_blocked(self):
        with pytest.raises(ForbiddenSQLError, match="root"):
            guard_sql("DROP USER root")

    def test_drop_other_role_allowed(self):
        guard_sql("DROP ROLE analyst")

    def test_drop_other_user_allowed(self):
        guard_sql("DROP USER testuser")

    def test_empty_sql_passes(self):
        guard_sql("")
