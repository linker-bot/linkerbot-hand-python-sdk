"""O6 04-series identity and tactile-capability regression tests."""

from __future__ import annotations

from linkerbot.hand.o6 import parse_o6_serial_identity
from linkerbot_hand import (
    DeviceIdentity,
    HandDevice,
    HandSide,
    SensorType,
    get_model_capabilities,
)
from linkerbot_hand.capabilities import specialize_model_capabilities
from linkerbot_hand.testing import FakeBackendFactory


def test_parse_o6_04_b_serial_identity() -> None:
    identity = parse_o6_serial_identity("LHO6-04.1-730-R-B-1-D")

    assert identity.product_model == "LHO6"
    assert identity.product_version == "04.1"
    assert identity.serial_number == "730"
    assert identity.hand_side == "right"
    assert identity.sensor_type == "B"
    assert identity.sensor_revision == "1"
    assert identity.factory_code == "D"
    assert identity.capability_variant == "04-B"
    assert identity.force_sensor_shape == (10, 4)


def test_parse_incomplete_o6_serial_identity_is_non_fatal() -> None:
    identity = parse_o6_serial_identity("LHO6")

    assert identity.product_model == "LHO6"
    assert identity.product_version is None
    assert identity.capability_variant is None
    assert identity.force_sensor_shape is None


def test_o6_04_b_keeps_tactile_matrix_capability() -> None:
    capabilities = specialize_model_capabilities(
        get_model_capabilities("O6", side="right"),
        DeviceIdentity(
            model="O6",
            firmware="V3.1.2",
            side=HandSide.RIGHT,
            serial_number="LHO6-04.1-730-R-B-1-D",
            capability_variant="04-B",
        ),
    )

    force = capabilities.sensor(SensorType.FORCE)
    assert force is not None
    assert force.matrix_shape == (10, 4)


def test_o6_04_unimplemented_sensor_variant_does_not_advertise_force() -> None:
    capabilities = specialize_model_capabilities(
        get_model_capabilities("O6", side="right"),
        DeviceIdentity(
            model="O6",
            firmware="V3.1.2",
            side=HandSide.RIGHT,
            serial_number="LHO6-04.1-730-R-Z-1-D",
            capability_variant="04-Z",
        ),
    )

    assert not capabilities.supports_sensor(SensorType.FORCE)


def test_o6_04_b_identity_specializes_live_device_capabilities() -> None:
    capabilities = get_model_capabilities("O6", side="right")
    factory = FakeBackendFactory(capabilities)
    factory.state.identity = DeviceIdentity(
        model="O6",
        firmware="V3.1.2",
        side=HandSide.RIGHT,
        serial_number="LHO6-04.1-730-R-B-1-D",
        capability_variant="04-B",
    )
    device = HandDevice(capabilities, factory)

    device.open()
    try:
        live = device.get_capabilities()
        force = live.sensor(SensorType.FORCE)
        assert live.side is HandSide.RIGHT
        assert live.firmware == "V3.1.2"
        assert force is not None
        assert force.matrix_shape == (10, 4)
    finally:
        device.close()
