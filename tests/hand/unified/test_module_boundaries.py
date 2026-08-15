"""Compatibility imports and registration requirements for modular hand code."""

import linkerbot_hand
from linkerbot.hand import device as compatibility_device
from linkerbot_hand.backends.legacy import (
    LEGACY_MODEL_ADAPTERS,
    LegacyHandBackend,
    create_hand_device,
)
from linkerbot_hand.backends.legacy.adapters import get_legacy_model_adapter
from linkerbot_hand.capabilities import (
    MODEL_CAPABILITIES,
    get_model_capabilities,
)
from linkerbot_hand.commands.scheduler import CommandScheduler
from linkerbot_hand.models import (
    MODEL_CAPABILITIES as COMPATIBILITY_MODEL_CAPABILITIES,
)
from linkerbot_hand.models import (
    get_model_capabilities as compatibility_get_model_capabilities,
)
from linkerbot_hand.scheduler import CommandScheduler as CompatibilityScheduler
from linkerbot_hand.session import HandDevice, HandSession
from linkerbot_hand.types import PollingPlan


def test_legacy_module_reexports_backend_and_factory() -> None:
    assert compatibility_device.LegacyHandBackend is LegacyHandBackend
    assert compatibility_device.create_hand_device is create_hand_device


def test_public_compatibility_modules_reexport_canonical_objects() -> None:
    assert linkerbot_hand.HandDevice is HandDevice
    assert linkerbot_hand.HandSession is HandSession
    assert linkerbot_hand.PollingPlan is PollingPlan
    assert COMPATIBILITY_MODEL_CAPABILITIES is MODEL_CAPABILITIES
    assert compatibility_get_model_capabilities is get_model_capabilities
    assert CompatibilityScheduler is CommandScheduler


def test_every_capability_model_has_a_complete_legacy_adapter() -> None:
    assert set(LEGACY_MODEL_ADAPTERS) == set(MODEL_CAPABILITIES)

    for model, capabilities in MODEL_CAPABILITIES.items():
        adapter = get_legacy_model_adapter(model)
        assert adapter is LEGACY_MODEL_ADAPTERS[model]
        assert adapter.model == capabilities.model
        assert adapter.module_name.startswith("linkerbot.hand.")
        assert adapter.class_name
        assert isinstance(adapter.classic_raw_fallback, bool)
        assert callable(adapter.prepare_options)
        assert callable(adapter.read_identity)
