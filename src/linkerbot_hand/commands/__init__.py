"""Command contracts and scheduling primitives."""

from .model import (
    CommandHandle,
    CommandTransition,
    ErrorInfo,
    HandCommand,
    coerce_timeout,
)

__all__ = [
    "CommandHandle",
    "CommandTransition",
    "ErrorInfo",
    "HandCommand",
    "coerce_timeout",
]
