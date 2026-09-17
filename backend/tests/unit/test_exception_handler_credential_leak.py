"""Regression tests for the credential leak via the ``NovaException`` handler.

The bug these cover: ``nova_exception_handler`` returned a plain
``JSONResponse``, so it never reached ``SanitizingJSONResponse``. StarRocks
echoes the statement it rejected inside its error text, and for a ``@stage``
query that statement carries the injected storage credentials — so
``POST /api/v1/query/explain`` answered HTTP 400 with ``aws.s3.access_key`` and
``aws.s3.secret_key`` in the ``detail`` field, unredacted.

The handler is global, so the leak was never specific to ``/query/explain``:
every route that can fail with an engine message went through it. These tests
therefore assert on the **HTTP response body** of a real app — the same shape
``test_explain_credential_leak.py`` uses — rather than on the sanitizing class
in isolation, so dropping the class from the handler fails them.

Values are placeholders, never a real credential.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import (
    ForbiddenSQLError,
    NovaException,
    SessionExpiredError,
    SetupRequiredError,
    StarRocksError,
    register_exception_handlers,
)

ACCESS_KEY = "AKIA_ERRPATH_TESTVALUE_123"
SECRET_KEY = "SECRET_ERRPATH_TESTVALUE_456"
REDACTED = "***"
S3_PATH = "s3://stages/error_path_probe.csv"

#: The statement StarRocks echoes back, credentials and all. Hand-built rather
#: than produced by the injector so the probe is independent of the translation
#: pipeline — this is the string the engine would put in ``exc.message``.
ENGINE_STATEMENT = (
    f"FILES('path'='{S3_PATH}', 'aws.s3.access_key'='{ACCESS_KEY}', "
    f"'aws.s3.secret_key'='{SECRET_KEY}', 'format'='csv')"
)

#: A well-formed engine failure that carries no credential.
BENIGN_ENGINE_MESSAGE = "Syntax error near 'FORM'; unexpected token"


@pytest.fixture
def error_client():
    """A real app whose handlers are registered exactly as production does.

    No engine, stage metadata or session lookup is involved: each probe route
    raises the ``NovaException`` the handler must sanitise, so the only thing
    under test is the handler's own response path.
    """
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/__engine_error")
    async def _engine_error():
        raise StarRocksError(f"Syntax error near {ENGINE_STATEMENT}")

    @app.get("/__benign_engine_error")
    async def _benign_engine_error():
        raise StarRocksError(BENIGN_ENGINE_MESSAGE)

    @app.get("/__forbidden")
    async def _forbidden():
        raise ForbiddenSQLError("ACCOUNTADMIN role cannot be dropped")

    @app.get("/__session_expired")
    async def _session_expired():
        raise SessionExpiredError()

    @app.get("/__setup_required")
    async def _setup_required():
        raise SetupRequiredError()

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class TestEngineErrorCarriesNoCredentials:
    """The reported finding: the global handler shipped the raw statement."""

    def test_response_body_carries_no_credentials(self, error_client):
        resp = error_client.get("/__engine_error")

        # Shape is preserved — this is still the 400 the contract documents.
        assert resp.status_code == 400
        body = resp.text
        assert ACCESS_KEY not in body, "storage access key leaked in the error body"
        assert SECRET_KEY not in body, "storage secret key leaked in the error body"

    def test_envelope_and_type_are_preserved(self, error_client):
        payload = error_client.get("/__engine_error").json()

        assert payload["type"] == "StarRocksError"
        assert set(payload) == {"detail", "type"}

    def test_redaction_is_value_only(self, error_client):
        """A client must still be able to read *why* the statement failed."""
        detail = error_client.get("/__engine_error").json()["detail"]

        assert "Syntax error near" in detail
        assert S3_PATH in detail
        assert "'format'='csv'" in detail
        assert f"'aws.s3.access_key'='{REDACTED}'" in detail
        assert f"'aws.s3.secret_key'='{REDACTED}'" in detail


class TestNonCredentialMessagesSurviveIntact:
    """Redaction must not mangle an error that had nothing to hide."""

    def test_benign_message_is_unchanged(self, error_client):
        payload = error_client.get("/__benign_engine_error").json()

        assert payload["detail"] == BENIGN_ENGINE_MESSAGE
        assert REDACTED not in payload["detail"]


class TestStatusContractIsUnchanged:
    """The handler serves more than engine failures; their codes must not move."""

    def test_forbidden_is_403(self, error_client):
        resp = error_client.get("/__forbidden")

        assert resp.status_code == 403
        assert resp.json() == {
            "detail": "ACCOUNTADMIN role cannot be dropped",
            "type": "ForbiddenSQLError",
        }

    def test_session_expired_is_401(self, error_client):
        resp = error_client.get("/__session_expired")

        assert resp.status_code == 401
        assert resp.json()["type"] == "SessionExpiredError"

    def test_setup_required_is_200(self, error_client):
        resp = error_client.get("/__setup_required")

        assert resp.status_code == 200
        assert resp.json()["type"] == "SetupRequiredError"


class TestHandlerStaysOnTheSanitizingBoundary:
    """Guards against the response class being swapped back to ``JSONResponse``."""

    def test_base_nova_exception_is_sanitized_too(self, error_client):
        """The class of bug, not just ``StarRocksError``."""

        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/__base")
        async def _base():
            raise NovaException(f"rejected: {ENGINE_STATEMENT}", status_code=400)

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/__base")

        assert resp.status_code == 400
        assert ACCESS_KEY not in resp.text
        assert SECRET_KEY not in resp.text

    def test_handler_uses_the_sanitizing_response_class(self):
        from app.common.responses import SanitizingJSONResponse

        app = FastAPI()
        register_exception_handlers(app)

        handler = app.exception_handlers[NovaException]
        # The handler closes over the class; asserting on the registered handler
        # itself is what makes a revert to ``JSONResponse`` fail here.
        assert SanitizingJSONResponse.__name__ in handler.__code__.co_names

    def test_router_reexports_the_moved_class(self):
        """The old import path must keep working for callers and tests."""
        from app.common.responses import SanitizingJSONResponse as Moved
        from app.modules.query.router import SanitizingJSONResponse as Reexported

        assert Moved is Reexported
