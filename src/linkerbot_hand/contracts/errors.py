"""Serializable error information exposed in command and health state."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import ErrorCode, HandSDKError


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    """Serializable description of an SDK or unexpected exception.

    Attributes:
        code: Stable error category that callers can map without parsing text.
        message: Human-readable error detail.
        error_type: Concrete Python exception class name.
    """

    code: ErrorCode
    message: str
    error_type: str

    @classmethod
    def from_exception(cls, error: BaseException) -> ErrorInfo:
        """Build error information from any exception.

        Args:
            error: Exception to convert. Non-SDK exceptions use
                :attr:`ErrorCode.INTERNAL`.

        Returns:
            A transport-neutral, serializable error description.
        """
        code = error.code if isinstance(error, HandSDKError) else ErrorCode.INTERNAL
        return cls(code=code, message=str(error), error_type=type(error).__name__)


__all__ = ["ErrorInfo"]
