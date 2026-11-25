"""Typed errors and stable CLI exit codes."""

from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UPSTREAM = 4
EXIT_SCHEMA = 5
EXIT_AUTH = 6
EXIT_PARTIAL = 7


class SeatfinderError(Exception):
    """Base error carrying a machine-readable code and exit status."""

    code = "E_UNKNOWN"
    exit_code = EXIT_UPSTREAM
    retryable = False

    def __init__(self, message: str, *, hint: str | None = None, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.detail = detail

    def to_dict(self) -> dict:
        payload = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.hint:
            payload["hint"] = self.hint
        if self.detail:
            payload["detail"] = self.detail
        return payload


class UsageError(SeatfinderError):
    code = "E_USAGE"
    exit_code = EXIT_USAGE


class UpstreamError(SeatfinderError):
    code = "E_UPSTREAM"
    exit_code = EXIT_UPSTREAM
    retryable = True


class BlockedError(UpstreamError):
    code = "E_BLOCKED"
    exit_code = EXIT_UPSTREAM
    retryable = True


class AuthError(SeatfinderError):
    code = "E_AUTH"
    exit_code = EXIT_AUTH
    retryable = True


class SchemaError(SeatfinderError):
    code = "E_SCHEMA"
    exit_code = EXIT_SCHEMA


class NotFoundError(SeatfinderError):
    code = "E_NOT_FOUND"
    exit_code = EXIT_USAGE


class PartialError(SeatfinderError):
    code = "E_PARTIAL"
    exit_code = EXIT_PARTIAL
