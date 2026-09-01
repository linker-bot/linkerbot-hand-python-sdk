"""Stable errors raised by explicit hand discovery operations."""

from __future__ import annotations

from ..api.errors import ErrorCode, HandError


class DiscoveryError(HandError):
    """Base error for discovery configuration and execution failures."""

    code = ErrorCode.DISCOVERY


class NoHandFoundError(DiscoveryError):
    """Raised when a discovery report contains no hand candidates."""

    code = ErrorCode.NO_HAND_FOUND


class AmbiguousHandError(DiscoveryError):
    """Raised when a caller requests one hand but several were discovered."""

    code = ErrorCode.AMBIGUOUS_HAND


class IncompleteIdentityError(DiscoveryError):
    """Raised when a partial candidate lacks required identity evidence."""

    code = ErrorCode.INCOMPLETE_IDENTITY


__all__ = [
    "AmbiguousHandError",
    "DiscoveryError",
    "IncompleteIdentityError",
    "NoHandFoundError",
]
