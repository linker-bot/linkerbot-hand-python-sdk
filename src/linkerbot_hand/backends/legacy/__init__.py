"""Compatibility backend for the existing model-specific drivers."""

from .adapters import LEGACY_MODEL_ADAPTERS, LegacyModelAdapter
from .backend import LegacyHandBackend
from .factory import create_hand_device

__all__ = [
    "LEGACY_MODEL_ADAPTERS",
    "LegacyHandBackend",
    "LegacyModelAdapter",
    "create_hand_device",
]
