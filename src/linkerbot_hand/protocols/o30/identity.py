"""Compatibility entry for O30 identity using the shared HOP implementation."""

from ..hop.identity import identity_transaction as o30_identity_transaction

__all__ = ["o30_identity_transaction"]
