"""Canonical public tactile selectors, shared without protocol dependencies."""

from typing import Literal, TypeAlias

from .errors import ValidationError

ForceField: TypeAlias = Literal["fn", "fns", "ft", "fts", "fd", "fds", "px"]
FORCE_FIELDS: tuple[ForceField, ...] = ("fn", "fns", "ft", "fts", "fd", "fds", "px")
DEFAULT_FORCE_FIELDS: tuple[ForceField, ...] = ("fn", "fns")


def validate_force_field(name: str) -> None:
    if not isinstance(name, str) or name not in FORCE_FIELDS:
        raise ValidationError(
            "force fields must be lowercase fn, fns, ft, fts, fd, fds or px; "
            "pass the timeout as timeout=..."
        )
