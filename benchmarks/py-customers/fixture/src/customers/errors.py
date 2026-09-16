"""Application error types.

Every error raised by this package derives from :class:`AppError`. The API layer
maps ``AppError`` subclasses onto HTTP responses using ``code``, so raising a bare
builtin exception produces a 500 instead of a useful client error.
"""


class AppError(Exception):
    """Base class for every error this package raises."""

    code = "app_error"


class ValidationError(AppError):
    """Raised when an input field fails validation."""

    code = "validation_error"

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


class NotFoundError(AppError):
    """Raised when a record does not exist."""

    code = "not_found"

    def __init__(self, entity: str, identifier: str) -> None:
        super().__init__(f"{entity} {identifier!r} not found")
        self.entity = entity
        self.identifier = identifier
