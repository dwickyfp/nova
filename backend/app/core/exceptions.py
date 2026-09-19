"""Global exception handlers for FastAPI."""

from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    # Only for the return annotation. The class cannot be imported at module
    # scope: it pulls in ``app.common.sql_guard``, which imports
    # ``ForbiddenSQLError`` from *this* module — a cycle that fails app boot.
    from app.common.responses import SanitizingJSONResponse


class NovaException(Exception):
    """Base exception for Nova backend."""

    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class InvalidCredentialsError(NovaException):
    """Authentication failed — wrong username or password."""

    def __init__(self, message: str = "Invalid credentials"):
        super().__init__(message, status_code=401)


class SessionExpiredError(NovaException):
    """Session not found or expired in Redis."""

    def __init__(self, message: str = "Session expired"):
        super().__init__(message, status_code=401)


class ForbiddenSQLError(NovaException):
    """SQL statement blocked by guard (e.g. ACCOUNTADMIN protection)."""

    def __init__(self, message: str = "Operation not permitted"):
        super().__init__(message, status_code=403)


class InsufficientRoleError(NovaException):
    """User lacks required role for this operation."""

    def __init__(self, message: str = "Insufficient privileges"):
        super().__init__(message, status_code=403)


class SetupRequiredError(NovaException):
    """First login — admin password must be changed."""

    def __init__(self, message: str = "Setup required"):
        super().__init__(message, status_code=200)  # 200 with SETUP_REQUIRED status


class StarRocksError(NovaException):
    """StarRocks query execution error."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message, status_code=status_code)


class StorageError(NovaException):
    """S3/MinIO storage operation error.

    Defaults to 500 only for genuinely unexpected shapes. Callers that can
    classify the underlying failure should pass the specific status (503 for
    an unreachable/misconfigured endpoint, 404 for a missing object, 502 for
    an upstream rejection) so the client never sees a bare 500.
    """

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message, status_code=status_code)


class StorageUnavailableError(StorageError):
    """Storage backend unreachable or misconfigured (endpoint down, bad creds).

    503 rather than 500: the failure is on the storage dependency, not in
    Nova's own logic, and the client can retry once an operator restores it.
    The message is always a Nova-authored summary — never the raw client
    error, which echoes the endpoint and access key.
    """

    def __init__(self, message: str):
        super().__init__(message, status_code=503)


class WorkspaceNotReadyError(StorageError):
    """NOVA_SYSTEM (or the workspace table) is not initialised yet.

    503 with an actionable message, so a user who hits the console before
    bootstrap finishes sees "not ready yet" instead of a bare 500.
    """

    def __init__(self, message: str):
        super().__init__(message, status_code=503)


def register_exception_handlers(app: FastAPI) -> None:
    """Register all custom exception handlers on the FastAPI app."""

    @app.exception_handler(NovaException)
    async def nova_exception_handler(
        request: Request, exc: NovaException
    ) -> "SanitizingJSONResponse":
        # Deliberately *not* a plain ``JSONResponse``: ``exc.message`` can carry
        # an engine error that echoes the statement StarRocks rejected, and for
        # a ``@stage`` query that statement has the injected storage credentials
        # in it. ``SanitizingJSONResponse`` reruns the same value-only,
        # fail-closed redaction the success path uses, so this handler — which
        # is global and therefore the error path of *every* route — is not the
        # one call site that forgets. Reverting this to ``JSONResponse``
        # reintroduces NOVA-21.
        #
        # The deferred import is deliberate too: ``exceptions`` and
        # ``app.common.sql_guard`` are mutually dependent, so the response class
        # — which pulls the redactor back in — is only importable once this
        # module is fully defined.
        from app.common.responses import SanitizingJSONResponse

        return SanitizingJSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message, "type": type(exc).__name__},
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> "SanitizingJSONResponse":
        # NOVA-94 security finding #2. FastAPI's built-in ``HTTPException``
        # handler renders with a plain ``JSONResponse``, so a module that
        # surfaces a service error as ``raise HTTPException(400, detail=str(exc))``
        # bypasses ``SanitizingJSONResponse`` entirely. ``detail`` is routinely
        # the engine's own error text, and StarRocks echoes the rejected
        # statement in it — for a repository/catalog statement that text carries
        # the storage keys. Registering this handler applies the same
        # fail-closed value-only redaction to every ``HTTPException`` in the
        # app, including routes added later, which is why it is done here rather
        # than by re-plumbing each router to a ``NovaException``.
        #
        # ``detail`` is preserved (not stringified) because FastAPI allows any
        # JSON-able detail; the sanitizer walks dicts/lists recursively.
        from app.common.responses import SanitizingJSONResponse

        return SanitizingJSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "type": "InternalServerError"},
        )
