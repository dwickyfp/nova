import ast
import csv

from scripts.inventory_control_plane import (
    CREATE,
    inspect_python,
    literal_bindings,
    merge_feature_inventory,
    string_template,
)


def test_docstrings_comments_and_dynamic_prefixes_are_not_tables():
    refs, calls = inspect_python(
        '''
"""NOVA_SYSTEM.EVERY table is private."""
# NOVA_SYSTEM.NOT_A_TABLE
prefix = "NOVA_SYSTEM.CONFIG_"
async def read():
    return await engine.execute_system("SELECT * FROM `NOVA_SYSTEM`.`CONFIG_TASKS`")
''',
        "sample.py",
    )
    assert set(refs) == {"CONFIG_TASKS"}
    assert calls[0]["receiver"] == "engine"
    assert calls[0]["literal_tables"] == ["CONFIG_TASKS"]


def test_constant_fstring_table_names_are_resolved_without_executing_source():
    source = """
TABLE = "NOVA_SYSTEM.CONFIG_DATAVIZ_PUBLICATIONS"
DDL = f"CREATE TABLE IF NOT EXISTS {TABLE} (id BIGINT)"
raise RuntimeError("must not execute")
async def read():
    return await db.execute_system(f"SELECT * FROM {TABLE} WHERE id = %s", [1])
"""
    refs, calls = inspect_python(source, "publication.py")
    assert set(refs) == {"CONFIG_DATAVIZ_PUBLICATIONS"}
    assert calls[0]["literal_tables"] == ["CONFIG_DATAVIZ_PUBLICATIONS"]
    bindings = literal_bindings(ast.parse(source))
    assert CREATE.findall(bindings["DDL"]) == ["CONFIG_DATAVIZ_PUBLICATIONS"]


def test_dynamic_identifier_stays_unresolved_and_requires_review():
    refs, calls = inspect_python(
        """
async def read(table):
    return await db.execute_system(f"SELECT * FROM {table}")
""",
        "dynamic.py",
    )
    assert refs == {}
    assert calls[0]["literal_tables"] == []
    assert calls[0]["review_status"] == "REQUIRES_CLASSIFICATION"
    assert (
        string_template(ast.parse('f"NOVA_SYSTEM.{name}"', mode="eval").body)
        == "NOVA_SYSTEM.{dynamic}"
    )


def test_enclosing_function_candidates_do_not_turn_dynamic_sql_into_reviewed_sql():
    source = """
class Repository:
    async def load(self, user_query):
        query = "SELECT * FROM NOVA_SYSTEM.CONFIG_TASKS"
        if user_query:
            query = user_query
        return await db.execute_system(query)
"""
    _, calls = inspect_python(source, "repository.py")
    call = calls[0]
    assert call["function"] == "Repository.load"
    assert call["literal_tables"] == []
    assert call["statement_kind"] == "DYNAMIC"
    assert call["function_candidate_tables"] == ["CONFIG_TASKS"]
    assert call["review_status"] == "REQUIRES_CLASSIFICATION"
    _, shifted = inspect_python("\n" + source, "repository.py")
    assert shifted[0]["callsite_sha256"] == call["callsite_sha256"]
    assert shifted[0]["source_file_sha256"] != call["source_file_sha256"]


def test_inventory_refresh_preserves_failures_waivers_and_removed_features(tmp_path):
    path = tmp_path / "features.csv"
    old = [
        {"feature": "docs:existing", "result": "FAIL", "evidence": "case-12"},
        {"feature": "embedding", "result": "OWNER_WAIVED", "evidence": "owner decision"},
    ]
    merge_feature_inventory(path, old)
    merge_feature_inventory(
        path,
        [
            {"feature": "docs:existing", "result": "NOT_RUN", "evidence": ""},
            {"feature": "api:new", "result": "NOT_RUN", "evidence": ""},
        ],
    )
    with path.open() as file:
        rows = list(csv.DictReader(file))
    assert rows[:2] == old
    assert rows[2]["feature"] == "api:new"


def test_injected_aliases_and_wrappers_are_inventoried_without_cross_class_leakage():
    _, calls = inspect_python('''
class Claims:
    def __init__(self, execute=None):
        if execute is None:
            execute = db.execute_system
        self._execute = execute
    async def read(self):
        return await self._execute("SELECT * FROM NOVA_SYSTEM.AUDIT_STREAM_CLAIMS")
class Catalog:
    def __init__(self, execute=None):
        self._execute = execute or db.execute_system
    async def read(self):
        return await self._execute("SHOW CATALOGS")
class Wrapper:
    async def _execute(self, sql, params=None):
        return await db.execute_system(sql, params)
    async def read(self):
        return await self._execute("SELECT * FROM NOVA_SYSTEM.CONFIG_STREAM_SOURCES")
class CallerData:
    async def read(self):
        return await self._execute("SELECT * FROM user_data")
''', "adapters.py")
    assert len(calls) == 4
    functions = {call["function"]: call for call in calls}
    assert functions["Claims.read"]["literal_tables"] == ["AUDIT_STREAM_CLAIMS"]
    assert functions["Catalog.read"]["statement_kind"] == "SHOW"
    assert functions["Wrapper._execute"]["dispatch"] == "direct"
    assert functions["Wrapper.read"]["literal_tables"] == ["CONFIG_STREAM_SOURCES"]
    assert all(call["review_status"] == "REQUIRES_CLASSIFICATION" for call in calls)


def test_raw_connection_and_mixed_metadata_cursor_bypass_are_inventoried():
    _, calls = inspect_python('''
import asyncmy
class Users:
    async def connect(self):
        return await asyncmy.connect(user=admin_name, password=secret)
    async def list(self, cursor, query):
        await cursor.execute("SHOW USERS")
        await cursor.execute("SELECT MAX(event_time) FROM NOVA_SYSTEM.AUDIT_LOG")
        await cursor.execute(query)
class Data:
    async def list(self, cursor, query):
        await cursor.execute(query)
''', "users.py")
    assert len(calls) == 4
    assert calls[0]["dispatch"] == "connection_candidate"
    cursors = calls[1:]
    assert all(call["dispatch"] == "metadata_cursor_candidate" for call in cursors)
    assert [call["statement_kind"] for call in cursors] == ["SHOW", "SELECT", "DYNAMIC"]
    assert cursors[-1]["literal_tables"] == []
    assert cursors[-1]["function_candidate_tables"] == ["AUDIT_LOG"]
    assert all(call["review_status"] == "REQUIRES_CLASSIFICATION" for call in calls)


def test_cursor_batch_constants_and_dynamic_metadata_namespace_require_review():
    _, calls = inspect_python('''
INSERT = "INSERT INTO NOVA_SYSTEM.CONFIG_TASKS(id) VALUES (%s)"
async def write(cur, table, rows):
    await cur.executemany(INSERT, rows)
    await cur.execute(f"SELECT * FROM NOVA_SYSTEM.{table}")
async def unrelated(cur):
    """NOVA_SYSTEM.NOT_SQL is a historical description."""
    await cur.execute("SELECT 1")
''', "raw.py")
    assert len(calls) == 2
    assert calls[0]["literal_tables"] == ["CONFIG_TASKS"]
    assert calls[1]["literal_tables"] == []
    assert all(call["dispatch"] == "metadata_cursor_candidate" for call in calls)
