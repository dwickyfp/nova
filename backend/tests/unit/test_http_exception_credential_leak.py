"""Regression tests for the credential leak via FastAPI's ``HTTPException``.

NOVA-94 security finding #2. ``register_exception_handlers`` wrapped only
``NovaException`` in ``SanitizingJSONResponse``; the built-in
``HTTPException`` handler kept using a plain ``JSONResponse``. Every new module
(backup, governance, resource_groups, variables) surfaces an engine/service
failure as ``raise HTTPException(400, detail=str(exc))`` — and the engine's
error text echoes the statement it rejected, which for a repository or catalog
statement carries the storage keys. So a BAD REQUEST could ship the keys even
though the 200 path was clean.

These assert on the HTTP response body of a real app with the production
handlers registered, so reverting the handler to a plain ``JSONResponse``
fails them.

Values are placeholders, never a real credential.
"""

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.exceptions import register_exception_handlers

ACCESS_KEY = "AKIA_HTTPERR_TESTVALUE_123"
SECRET_KEY = "SECRET_HTTPERR_TESTVALUE_456"
REDACTED = "***"

ENGINE_STATEMENT = (
    'CREATE REPOSITORY `r` WITH BROKER ON LOCATION "s3://b/p" '
    f'PROPERTIES("aws.s3.access_key" = "{ACCESS_KEY}", '
    f'"aws.s3.secret_key" = "{SECRET_KEY}")'
)


def _client_with_bad_request():
    """A real app whose 400 handler is the one production registers."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/__boom")
    async def _boom():
        raise HTTPException(status_code=400, detail=f"engine rejected: {ENGINE_STATEMENT}")

    return TestClient(app, raise_server_exceptions=False)


class TestHTTPExceptionBodyIsSanitized:
    def test_no_credential_reaches_the_400_body(self):
        resp = _client_with_bad_request().post("/__boom")

        assert resp.status_code == 400
        assert ACCESS_KEY not in resp.text, "access key leaked via HTTPException"
        assert SECRET_KEY not in resp.text, "secret key leaked via HTTPException"

    def test_redaction_is_value_only(self):
        """The client must still be able to read why its request failed."""
        detail = _client_with_bad_request().post("/__boom").json()["detail"]

        assert "engine rejected" in detail
        assert "CREATE REPOSITORY" in detail
        assert REDACTED in detail


class TestHTTPExceptionShapeIsPreserved:
    def test_detail_and_status_pass_through(self):
        resp = _client_with_bad_request().post("/__boom")

        assert resp.status_code == 400
        assert "engine rejected" in resp.json()["detail"]

    def test_non_string_detail_is_untouched(self):
        """FastAPI allows a JSON-able ``detail``; the sanitizer must not choke."""
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/__structured")
        async def _structured():
            raise HTTPException(status_code=422, detail={"field": "name", "code": "required"})

        resp = TestClient(app, raise_server_exceptions=False).get("/__structured")

        assert resp.status_code == 422
        assert resp.json()["detail"] == {"field": "name", "code": "required"}
