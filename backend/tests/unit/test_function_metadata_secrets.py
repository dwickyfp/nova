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
    from app.modules.llm_functions.service import LLMFunctionService

    service = LLMFunctionService()
    monkeypatch.setattr(
        service,
        "_get_provider",
        AsyncMock(
            return_value={
                "name": "fixture",
                "type": "openai",
                "api_key": "sentinel-key",
                "endpoint": "https://example.com/v1",
            }
        ),
    )
    monkeypatch.setattr("app.modules.llm_functions.service.decrypt_api_key", lambda value: value)
    monkeypatch.setattr(
        service,
        "_connect",
        AsyncMock(
            side_effect=RuntimeError('SQL failed: ai_query(prompt, \'{"api_key":"sentinel-key"}\')')
        ),
    )
    result = await service._register_single_udf(
        "complete",
        {
            "provider_id": "fixture",
            "model_name": "fixture-model",
        },
    )
    assert not result["registered"]
    assert "sentinel-key" not in str(result)
    assert "sentinel-key" not in caplog.text


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
