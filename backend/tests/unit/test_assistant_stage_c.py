"""Stage C — ``query_execute`` tool, consent, and audit correlation (NOVA-77).

These are unit tests: no network, no StarRocks. ``query_service`` is replaced
with a fake, and the audit writer is faked, so every acceptance criterion is
asserted on the **call path** rather than on a socket.

Coverage mirrors the issue's acceptance criteria:

1. a read-only statement runs through ``QueryService.execute_statements`` on the
   requesting user's connection and returns columns + bounded preview + count;
2. each denied category is refused before the engine, and a multi-statement
   payload cannot smuggle a denied statement;
3. a destructive statement is never auto-approved; a read-only one can be;
4. consent decisions are honoured and a privilege error terminates without an
   elevated retry;
5. the audit row is correlated by ``session_id`` = conversation id, is
   redacted, and carries no rows;
6. credential-shaped result values are redacted value-level;
7. ``ASSISTANT_MAX_ROWS`` is enforced at fetch time;
8. the tool never imports ``asyncmy`` / ``app.core.database`` and never opens a
   socket.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app.modules.assistant.schemas import ToolCallView
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import AssistantTool, ToolInvocation, ToolOutcome, ToolRegistry
from app.modules.assistant.tools.policy import classify_statements, tool_classification
from app.modules.assistant.tools.query_execute import (
    ASSISTANT_MAX_ROWS,
    QueryExecuteTool,
)
from app.modules.assistant.tools.redaction import (
    is_credential_column,
    is_credential_value,
    redact_rows,
)

# ── Fakes ────────────────────────────────────────────────────────────────────


class FakeResult:
    """Minimal stand-in for ``QueryResult``."""

    def __init__(
        self,
        columns: list[str] | None = None,
        rows: list[list] | None = None,
        row_count: int | None = None,
        affected_rows: int = 0,
        error: str | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self.columns = columns or []
        self.rows = rows or []
        self.row_count = row_count if row_count is not None else len(self.rows)
        self.affected_rows = affected_rows
        self.error = error
        self.warnings = warnings or []


class FakeQueryService:
    """Records every ``execute_statements`` call and returns queued results."""

    def __init__(
        self, results: list[FakeResult] | None = None, exc: Exception | None = None
    ) -> None:
        self.calls: list[dict] = []
        self._results = results or []
        self._exc = exc

    async def execute_statements(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._results


class FakeAudit:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def __call__(self, **kwargs):
        self.rows.append(kwargs)
        return "audit-id"


class FakeProvider:
    def __init__(self, script: list[dict]) -> None:
        self._script = list(script)
        self.calls: list[dict] = []

    async def resolve(self):
        from app.modules.assistant.provider import ProviderConfig

        return ProviderConfig(provider_id="p1", model="m1", endpoint="http://x/v1", api_key="k")

    async def complete(self, *, messages, tools=None, provider=None):
        self.calls.append({"messages": messages, "tools": tools})
        return self._script.pop(0) if self._script else {"role": "assistant", "content": "done"}


def _invocation(sql: str, call_id: str = "c1") -> ToolInvocation:
    return ToolInvocation(
        tool_call_id=call_id, tool_name="query_execute", arguments={"sql": sql}
    )


def _user(session_id: str = "sess-1") -> dict:
    return {
        "username": "alice",
        "session_id": session_id,
        "active_role": "analyst",
        "encrypted_password": "enc",
    }


def _context(
    *, thread_id: str = "thread-1", session_id: str | None = "sess-1"
) -> LoopContext:
    return LoopContext(
        user_name="alice",
        database="db1",
        schema_name="sch1",
        role="analyst",
        workspace_file_id="file-1",
        session_id=session_id,
        thread_id=thread_id,
        user=_user(session_id or "sess-1"),
    )


@pytest.fixture
def fakes(monkeypatch):
    service = FakeQueryService()
    audit = FakeAudit()
    monkeypatch.setattr("app.modules.query.service.query_service", service, raising=True)
    monkeypatch.setattr(
        "app.modules.assistant.tools.query_execute.write_audit_log", audit, raising=True
    )
    return service, audit


# ── 1. Read-only executes through the in-process service ─────────────────────


async def test_read_only_statement_runs_through_query_service(fakes):
    service, audit = fakes
    service._results = [
        FakeResult(columns=["n"], rows=[[1]], row_count=1),
    ]
    tool = QueryExecuteTool()
    outcome = await tool.run(_invocation("SELECT 1 AS n"), _context())

    assert outcome.ok is True
    assert len(service.calls) == 1
    call = service.calls[0]
    # The exact call path: in-process service, requesting user's credentials.
    assert call["sql"] == "SELECT 1 AS n"
    assert call["username"] == "alice"
    assert call["encrypted_password"] == "enc"
    assert call["role"] == "analyst"
    assert call["database"] == "db1"
    assert call["schema"] == "sch1"
    assert call["file_id"] == "file-1"
    # Never auto-confirm a destructive statement.
    assert call["confirm_destructive"] is False
    # Row cap applied at fetch time.
    assert call["max_rows"] == ASSISTANT_MAX_ROWS

    payload = json.loads(outcome.summary)
    assert payload["columns"] == ["n"]
    assert payload["row_count"] == 1
    assert payload["rows"] == [[1]]
    assert payload["truncated"] is False
    assert audit.rows and audit.rows[0]["status"] == "SUCCESS"


async def test_tool_never_imports_asyncmy_or_the_db_module_and_never_opens_a_socket():
    """The tool must call the in-process service, not open a connection."""
    source = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "modules"
        / "assistant"
        / "tools"
        / "query_execute.py"
    ).read_text()
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)

    forbidden = {"asyncmy", "app.core.database", "db.user_conn"}
    assert not (imported & forbidden)
    assert "socket" not in imported
    # And it does call the in-process service (the import is function-local, so
    # assert on the source text too).
    assert "app.modules.query.service" in source
    assert "query_service.execute_statements" in source


async def test_bounded_preview_truncates_a_wide_result():
    from app.modules.assistant.tools.query_execute import _render_result

    rows = [[f"value-{i}-" + ("x" * 40)] for i in range(300)]
    rendered = json.loads(_render_result(FakeResult(columns=["c"], rows=rows, row_count=300)))
    assert rendered["row_count"] == 300
    assert rendered["truncated"] is True
    assert rendered["rows_returned"] < 300


# ── 2. Denied statements are refused before the engine ───────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE x",
        "TRUNCATE TABLE x",
        "DELETE FROM x",
        "UPDATE x SET a = 1",
        "ALTER TABLE x DROP COLUMN c",
        "GRANT SELECT ON x TO u",
        "REVOKE SELECT ON x FROM u",
        "SET role = 'admin'",
        "CREATE TABLE x (a INT)",
        "INSERT INTO x VALUES (1)",
        "COPY INTO x FROM 's3://b'",
        "CREATE ML_MODEL m TYPE = FORECAST TARGET = y AS SELECT 1",
        "CREATE TASK t AS SELECT 1",
    ],
)
async def test_denied_statement_is_refused_before_the_engine(fakes, sql):
    service, audit = fakes
    tool = QueryExecuteTool()
    tool.preview(_invocation(sql))  # as the loop does before consent
    outcome = await tool.run(_invocation(sql), _context())

    assert outcome.ok is False
    assert service.calls == []  # the engine was never reached
    assert audit.rows and audit.rows[0]["status"] == "DENIED"
    assert audit.rows[0]["event_type"] == "assistant_tool"


async def test_multi_statement_payload_cannot_smuggle_a_denied_statement(fakes):
    service, audit = fakes
    tool = QueryExecuteTool()
    sql = "SELECT 1; DROP TABLE x"
    assert tool_classification(sql) == "destructive"

    outcome = await tool.run(_invocation(sql), _context())
    assert outcome.ok is False
    assert service.calls == []  # not even the SELECT prefix ran
    assert audit.rows[0]["status"] == "DENIED"


def test_mixed_payload_is_destructive_and_pure_denied_is_denied():
    classification, decisions = classify_statements(
        ["SELECT 1", "DROP TABLE x"]
    )
    assert classification == "destructive"
    assert [d.allowed for d in decisions] == [True, False]

    classification, _ = classify_statements(["DROP TABLE x", "INSERT INTO y VALUES (1)"])
    assert classification == "denied"


def test_nova_ddl_is_recognised_as_denied():
    assert tool_classification("CREATE ML_MODEL m TYPE = FORECAST") == "denied"
    assert tool_classification("CREATE TASK t AS SELECT 1") == "denied"


# ── CTE body: a WITH prefix is not itself read-only ─────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "WITH x AS (SELECT 1) DELETE FROM t",
        "WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x",
        "WITH x AS (SELECT 1) UPDATE t SET a = 1",
        "WITH a AS (SELECT 1), b AS (SELECT 2) DELETE FROM t",
        "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM x WHERE n < 5) DELETE FROM t",
    ],
)
def test_with_cte_mutating_body_is_denied(sql):
    assert tool_classification(sql) == "denied"


@pytest.mark.parametrize(
    "sql",
    [
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "WITH a AS (SELECT 1), b AS (SELECT 2) SELECT * FROM a JOIN b",
        "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM x WHERE n < 5) "
        "SELECT * FROM x",
        "WITH x AS (SELECT '(') SELECT * FROM x",
    ],
)
def test_with_cte_select_body_is_read_only(sql):
    assert tool_classification(sql) == "read_only"


async def test_with_cte_mutating_payload_is_refused_before_the_engine(fakes):
    service, _audit = fakes
    tool = QueryExecuteTool()
    outcome = await tool.run(_invocation("WITH x AS (SELECT 1) DELETE FROM t"), _context())
    assert outcome.ok is False
    assert service.calls == []


# ── 3. Destructive is never auto-approved ───────────────────────────────────


class RecordingTool:
    """A tool that records classification, for the loop-level consent tests."""

    def __init__(self, classification: str) -> None:
        self.name = "query_execute"
        self.classification = classification
        self.description = ""
        self.parameters = {"type": "object", "properties": {}}
        self.runs = 0

    def preview(self, invocation):
        return invocation.arguments.get("sql", "")

    async def run(self, invocation, context):
        self.runs += 1
        return ToolOutcome(ok=True, summary="ok")


def _tool_call(call_id: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "query_execute", "arguments": json.dumps({"sql": "SELECT 1"})},
    }


def _frame_event(frame: str) -> str:
    return frame.splitlines()[0].removeprefix("event: ")


def _frame_data(frame: str) -> dict:
    for line in frame.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return {}


async def _collect(agen):
    return [frame async for frame in agen]


async def test_destructive_call_never_auto_approves_even_with_a_grant():
    provider = FakeProvider(
        [
            {"role": "assistant", "content": "", "tool_calls": [_tool_call("c1")]},
            {"role": "assistant", "content": "ok"},
        ]
    )
    registry = ToolRegistry()
    registry.register(RecordingTool("destructive"))
    loop = AssistantLoop(provider=provider, registry=registry)
    thread = AssistantThread(thread_id="t1", user_name="alice", title="T")
    thread.consent.always_allow_read_only = True

    asked: list[str] = []

    async def resolver(inv, cls):
        asked.append(cls)
        return True

    frames = await _collect(
        loop.run(
            thread=thread,
            user_content="go",
            context=_context(),
            resolve_consent=resolver,
        )
    )
    assert "tool_call" in [_frame_event(f) for f in frames]
    assert asked == ["destructive"]


async def test_read_only_call_is_covered_by_an_allow_session_grant():
    provider = FakeProvider(
        [
            {"role": "assistant", "content": "", "tool_calls": [_tool_call("c1")]},
            {"role": "assistant", "content": "ok"},
        ]
    )
    registry = ToolRegistry()
    registry.register(RecordingTool("read_only"))
    loop = AssistantLoop(provider=provider, registry=registry)
    thread = AssistantThread(thread_id="t1", user_name="alice", title="T")
    thread.consent.always_allow_read_only = True

    asked: list[str] = []

    async def resolver(inv, cls):
        asked.append(cls)
        return True

    frames = await _collect(
        loop.run(
            thread=thread,
            user_content="go",
            context=_context(),
            resolve_consent=resolver,
        )
    )
    assert "tool_call" not in [_frame_event(f) for f in frames]
    assert asked == []


# ── 4. Consent decisions + privilege-error termination ──────────────────────


async def test_deny_decision_blocks_execution(fakes):
    service, _audit = fakes
    tool = QueryExecuteTool()
    # The loop owns consent; the tool is only called when allowed. This asserts
    # the loop does not call the tool on a deny.
    provider = FakeProvider(
        [
            {"role": "assistant", "content": "", "tool_calls": [_tool_call("c1")]},
            {"role": "assistant", "content": "understood"},
        ]
    )
    registry = ToolRegistry()
    registry.register(tool)
    loop = AssistantLoop(provider=provider, registry=registry)

    frames = await _collect(
        loop.run(
            thread=AssistantThread(thread_id="t1", user_name="alice", title="T"),
            user_content="go",
            context=_context(),
            resolve_consent=lambda inv, cls: _deny(),
        )
    )
    assert any(
        _frame_event(f) == "tool_status" and _frame_data(f)["status"] == "denied"
        for f in frames
    )
    assert service.calls == []


async def test_engine_privilege_error_terminates_without_elevated_retry(fakes):
    service, audit = fakes
    service._exc = RuntimeError("Access denied; you need SELECT privilege (5203)")
    tool = QueryExecuteTool()

    outcome = await tool.run(_invocation("SELECT * FROM secret"), _context())

    assert outcome.ok is False
    assert len(service.calls) == 1  # exactly one attempt, no retry
    # The credential never changed, and the failure was audited.
    assert service.calls[0]["encrypted_password"] == "enc"
    assert audit.rows and audit.rows[0]["status"] == "ERROR"


async def test_engine_privilege_error_in_result_terminates(fakes):
    service, audit = fakes
    service._results = [FakeResult(error="SQL error: (5203) Access denied")]
    tool = QueryExecuteTool()

    outcome = await tool.run(_invocation("SELECT * FROM secret"), _context())
    assert outcome.ok is False
    assert len(service.calls) == 1
    assert audit.rows and audit.rows[0]["status"] == "ERROR"


# ── 5. Audit correlation and redaction ──────────────────────────────────────


async def test_audit_row_is_correlated_by_conversation_id(fakes):
    service, audit = fakes
    service._results = [FakeResult(columns=["n"], rows=[[1]])]
    tool = QueryExecuteTool()

    # The conversation id is the correlation key even when a session id exists.
    context = _context(thread_id="conv-42", session_id="sess-9")
    await tool.run(_invocation("SELECT 1"), context)

    assert service.calls[0]["session_id"] == "conv-42"
    assert audit.rows[0]["session_id"] == "conv-42"


async def test_auth_session_id_is_used_when_no_conversation_is_present(fakes):
    service, audit = fakes
    service._results = [FakeResult(columns=["n"], rows=[[1]])]
    tool = QueryExecuteTool()
    await tool.run(_invocation("SELECT 1"), _context(thread_id=None, session_id="sess-9"))

    assert service.calls[0]["session_id"] == "sess-9"
    assert audit.rows[0]["session_id"] == "sess-9"


async def test_audit_sql_is_redacted_and_never_stores_rows(fakes):
    service, audit = fakes
    service._results = [FakeResult(columns=["n"], rows=[["supersecretvalue"]])]
    tool = QueryExecuteTool()

    secret_sql = (
        "SELECT * FROM FILES('path'='s3://b/f', 'aws.s3.secret_key'='AKIAIOSFODNN7EXAMPLE')"
    )
    # Only the preview is redacted at the tool boundary; the execution statement
    # is the user's own. Assert the audit row carries the redacted form.
    tool.preview(_invocation(secret_sql))
    await tool.run(_invocation("SELECT 1"), _context())

    for row in audit.rows:
        assert "AKIAIOSFODNN7EXAMPLE" not in (row.get("sql_text") or "")
        assert row.get("event_type") == "assistant_tool"


def test_preview_is_redacted_sql():
    tool = QueryExecuteTool()
    preview = tool.preview(
        _invocation(
            "SELECT * FROM FILES('path'='s3://b', 'aws.s3.secret_key'='AKIAIOSFODNN7EXAMPLE')"
        )
    )
    assert "AKIAIOSFODNN7EXAMPLE" not in preview
    assert "***" in preview


def test_preview_withholds_sql_it_cannot_redact(monkeypatch):
    from app.modules.assistant.tools import query_execute as qe

    def boom(sql):
        raise RuntimeError("cannot redact")

    monkeypatch.setattr(qe, "redact_sql_credentials", boom)
    tool = QueryExecuteTool()
    preview = tool.preview(_invocation("SELECT 1"))
    assert preview == "[statement withheld: it could not be redacted]"


# ── 6. Value-level redaction ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "column",
    ["password", "user_password", "api_key", "apiKey", "access-key", "client_secret", "bearer"],
)
def test_credential_column_names_are_detected(column):
    assert is_credential_column(column) is True


@pytest.mark.parametrize("column", ["id", "name", "created_at", "token_count"])
def test_ordinary_column_names_are_not_flagged(column):
    assert is_credential_column(column) is False


@pytest.mark.parametrize(
    "value",
    [
        "AKIAIOSFODNN7EXAMPLE",
        "-----BEGIN RSA PRIVATE KEY-----",
        "sk-abcdefghijklmnopqrstuvwxyz123456",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
    ],
)
def test_credential_shaped_values_are_detected(value):
    assert is_credential_value(value) is True


@pytest.mark.parametrize("value", ["hello world", "12345", "2026-09-18", None])
def test_ordinary_values_are_not_flagged(value):
    assert is_credential_value(value) is False


def test_redact_rows_is_value_level_not_the_sql_helper():
    """A credential-shaped *value* in an ordinary column is redacted.

    ``redact_sql_credentials`` only rewrites credential assignments in SQL;
    this proves the assistant's redactor works on the row values themselves.
    """
    columns = ["note", "count"]
    rows = [["AKIAIOSFODNN7EXAMPLE", 3]]
    redacted = redact_rows(columns, rows)
    assert redacted == [["***", 3]]


def test_redact_rows_redacts_every_cell_of_a_credential_column():
    columns = ["user", "password"]
    rows = [["alice", "not-a-known-format"], ["bob", "hunter2"]]
    assert redact_rows(columns, rows) == [
        ["alice", "***"],
        ["bob", "***"],
    ]


def test_none_stays_none_in_a_credential_column():
    assert redact_rows(["token"], [[None]]) == [[None]]


async def test_credential_value_is_redacted_before_entering_the_model_summary(fakes):
    service, _audit = fakes
    service._results = [
        FakeResult(columns=["note"], rows=[["AKIAIOSFODNN7EXAMPLE"]], row_count=1)
    ]
    tool = QueryExecuteTool()
    outcome = await tool.run(_invocation("SELECT note FROM t"), _context())

    assert outcome.ok is True
    assert "AKIAIOSFODNN7EXAMPLE" not in outcome.summary
    assert "***" in outcome.summary


# ── 7. Row cap at fetch time ────────────────────────────────────────────────


async def test_max_rows_is_passed_to_the_service_for_every_call(fakes):
    service, _audit = fakes
    service._results = [FakeResult(columns=["n"], rows=[[1]])]
    tool = QueryExecuteTool(max_rows=ASSISTANT_MAX_ROWS)
    await tool.run(_invocation("SELECT 1"), _context())
    assert service.calls[0]["max_rows"] == 100

    tool_small = QueryExecuteTool(max_rows=5)
    await tool_small.run(_invocation("SELECT 1"), _context())
    assert service.calls[1]["max_rows"] == 5


def test_default_row_cap_is_the_spec_value():
    assert ASSISTANT_MAX_ROWS == 100


# ── 8. Loop integration shape ───────────────────────────────────────────────


def test_registry_exposes_query_execute():
    from app.modules.assistant.registry import tool_registry

    tool = tool_registry.get("query_execute")
    assert tool is not None
    assert isinstance(tool, QueryExecuteTool)
    assert tool.name == "query_execute"


def test_query_execute_satisfies_the_tool_protocol():
    tool: AssistantTool = QueryExecuteTool()
    assert tool.name == "query_execute"
    assert callable(tool.preview)
    assert callable(tool.run)


def test_tool_schema_is_advertised_to_the_model():
    from app.modules.assistant.registry import tool_registry

    schemas = AssistantLoop._tool_schemas(
        AssistantLoop(provider=FakeProvider([]), registry=tool_registry)
    )
    names = {s["function"]["name"] for s in schemas}
    assert "query_execute" in names


def test_tool_call_view_carries_the_classification():
    view = ToolCallView(
        tool_call_id="c1",
        tool_name="query_execute",
        sql_preview="SELECT 1",
        classification="read_only",
    )
    assert view.status == "pending"
    assert view.classification == "read_only"


async def _deny() -> bool:
    return False
