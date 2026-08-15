"""Compatibility exports for the unified legacy-driver adapter."""

import importlib as importlib

from linkerbot_hand.backends.legacy import LegacyHandBackend, create_hand_device

__all__ = ["LegacyHandBackend", "create_hand_device"]
