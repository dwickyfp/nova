import json
from unittest.mock import AsyncMock

import pytest

from app.common.sql_guard import CredentialsRedactionError, redact_sql_credentials
from app.modules.query.repository import QueryResult
from app.proxy.executor import ProxyQueryExecutor
from app.proxy.session import SessionState


@pytest.mark.parametrize("secret", ["sentinel-key", 'sentinel-"quoted-tail', "sentinel-\\tail"])
def test_sql_udf_json_credentials_are_redacted_without_corrupting_json(secret):
    config = json.dumps({"model": "fixture", "api_key": secret, "max_tokens": 128})
    redacted = redact_sql_credentials(config)
    assert json.loads(redacted) == {"model": "fixture", "api_key": "***", "max_tokens": 128}
    assert redact_sql_credentials(redacted) == redacted
    assert secret not in redact_sql_credentials(f"ai_query(prompt, '{config}')")


def test_function_properties_cannot_expose_stored_keys_on_api_or_mysql():
    body = 'ai_query(prompt, \'{"api_key":"sentinel-key"}\')'
    result = QueryResult(
        columns=["Signature", "Return Type", "Function Type", "Intermediate Type", "Properties"],
        rows=[["ai_complete(VARCHAR)", "VARCHAR", "SQL", "NULL", body]],
        executed_sql="SHOW FULL GLOBAL FUNCTIONS",
    )
    assert "sentinel-key" not in str(result.rows)
    wire = ProxyQueryExecutor(SessionState())._to_wire([result])
    assert "sentinel-key" not in str(wire.rows)
    assert '"api_key":"***"' in wire.rows[0][4]


def test_ordinary_query_data_is_not_treated_as_function_metadata():
    value = '{"api_key":"user-supplied-data"}'
    result = QueryResult(columns=["document"], rows=[[value]], executed_sql="SELECT document")
    assert result.rows == [[value]]


def test_streamed_function_metadata_is_redacted_after_fetch():
    result = QueryResult(executed_sql="SHOW FULL GLOBAL FUNCTIONS")
    result.columns = ["Signature", "Function Type", "Properties"]
    result.rows = [["fn(VARCHAR)", "SQL", '{"api_key":"sentinel-key"}']]
    result.redact_metadata()
    assert "sentinel-key" not in str(result.rows)


def test_malformed_json_credential_refuses_readback():
    with pytest.raises(CredentialsRedactionError):
        redact_sql_credentials('{"api_key":unquoted-secret}')


async def test_udf_registration_failure_cannot_echo_resolved_config(monkeypatch, caplog):
    import hashlib
    import json
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from app.modules.llm_functions import registration_worker as module
    from app.modules.llm_functions.gateway import alias_token

    snapshot = {
        "contract_version": 1,
        "function_type": "complete",
        "mode": "configured",
        "alias": {"id": "alias-1"},
        "model": {"name": "fixture-model"},
    }
    payload = json.dumps(snapshot)
    definition = {
        "version": 1,
        "digest": hashlib.sha256(payload.encode()).hexdigest(),
        "payload": payload,
        "function_type": "complete",
    }
    job = {
        "id": uuid4(),
        "lease_holder": uuid4(),
        "lease_token": 1,
        "revision": 1,
        "state": "LEASED",
        "queue": "maintenance",
        "kind": module.JOB_KIND,
        "definition_ref": uuid4(),
        "definition_version": 1,
        "definition_digest": definition["digest"],
    }
    registrations = AsyncMock()
    registrations.definition.return_value = definition
    registrations.head.return_value = {"version": 1}
    jobs = AsyncMock()
    for method in ("prepare", "submitted", "verifying"):
        getattr(jobs, method).return_value = job
    jobs.block.return_value = {"state": "BLOCKED"}
    monkeypatch.setattr(module, "FunctionRegistrationRepository", lambda connection: registrations)
    monkeypatch.setattr(module, "JobRepository", lambda connection: jobs)

    @asynccontextmanager
    async def transaction():
        yield object()

    token = alias_token("alias-1")
    engine = AsyncMock()
    engine.apply.side_effect = RuntimeError(f"SQL failed with sentinel-key {token}")
    engine.stored_body.side_effect = RuntimeError(f"read failed with sentinel-key {token}")
    result = await module.AIFunctionRegistrationParticipant(
        engine=engine, transaction=transaction
    ).handle(job)
    assert result == "BLOCKED"
    engine.apply.assert_awaited_once()
    jobs.block.assert_awaited_once()
    jobs.record_outcome.assert_not_awaited()
    assert "sentinel-key" not in str(result) + caplog.text
    assert token not in str(result) + caplog.text


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE GLOBAL FUNCTION double_it(x INT) RETURNS x*2",
        "CREATE FUNCTION numeric_fn(INT) RETURNS INT "
        "PROPERTIES('symbol'='Fixture','file'='fixture.jar')",
    ],
)
def test_upstream_labeled_udf_rules_reach_native_planning(sql):
    from app.sql_frontend.analysis.semantics import default_semantics
    from app.sql_frontend.ast.builder import default_builders
    from app.sql_frontend.ast.statements import NativeStatement
    from app.sql_frontend.parser import parse_statement

    statement = default_builders().build(parse_statement(sql))
    assert isinstance(statement, NativeStatement)
    assert default_semantics().analyze(statement).effects.changes_schema
