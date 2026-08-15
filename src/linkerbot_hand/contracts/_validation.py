"""Shared validation primitives for public hand contracts."""

from __future__ import annotations

import math


def is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


__all__ = ["is_finite_number"]
