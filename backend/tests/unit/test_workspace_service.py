from app.common.sql_guard import is_destructive_sql, is_unscoped_mutation
from app.modules.workspaces.service import WorkspaceService


class TestWorkspaceService:
    def test_build_object_key_uses_username_root(self):
        service = WorkspaceService()
        key = service.build_object_key("analyst", "reports/weekly.sql")
        assert key == "workspaces/analyst/reports/weekly.sql"

    def test_build_object_key_handles_root_file(self):
        service = WorkspaceService()
        key = service.build_object_key("analyst", "Untitled.sql")
        assert key == "workspaces/analyst/Untitled.sql"


class TestWorkspaceSqlSafety:
    def test_drop_is_destructive(self):
        assert is_destructive_sql("DROP TABLE users")

    def test_delete_without_where_is_unscoped(self):
        assert is_unscoped_mutation("DELETE FROM users")

    def test_select_is_not_destructive(self):
        assert not is_destructive_sql("SELECT * FROM users")
