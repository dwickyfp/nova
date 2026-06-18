"""Global exception handlers for FastAPI."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


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
    """S3/MinIO storage operation error."""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message, status_code=status_code)


def register_exception_handlers(app: FastAPI) -> None:
    """Register all custom exception handlers on the FastAPI app."""

    @app.exception_handler(NovaException)
    async def nova_exception_handler(request: Request, exc: NovaException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message, "type": type(exc).__name__},
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "type": "InternalServerError"},
        )
