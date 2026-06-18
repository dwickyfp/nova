"""Auth-specific exceptions."""

from app.core.exceptions import NovaException


class InvalidCredentialsError(NovaException):
    def __init__(self, message: str = "Invalid username or password"):
        super().__init__(message, status_code=401)


class SetupAlreadyCompleteError(NovaException):
    def __init__(self, message: str = "Setup already completed"):
        super().__init__(message, status_code=400)


class PasswordMismatchError(NovaException):
    def __init__(self, message: str = "Passwords do not match"):
        super().__init__(message, status_code=400)


class WeakPasswordError(NovaException):
    def __init__(self, message: str = "Password must be at least 8 characters"):
        super().__init__(message, status_code=400)


class DefaultPasswordError(NovaException):
    def __init__(self, message: str = "Default password must be changed"):
        super().__init__(message, status_code=403)
