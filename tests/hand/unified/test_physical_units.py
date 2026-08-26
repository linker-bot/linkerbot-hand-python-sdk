from typing import Any, cast

import pytest

from linkerbot.hand.l6.angle import AngleManager as L6AngleManager
from linkerbot.hand.l6.speed import SpeedManager as L6SpeedManager
from linkerbot.hand.l6.torque import L6Torque
from linkerbot.hand.l20lite.angle import AngleManager as L20LiteAngleManager
from linkerbot.hand.l20lite.speed import SpeedManager as L20LiteSpeedManager
from linkerbot.hand.l25.angle import AngleManager as L25AngleManager
from linkerbot.hand.l25.speed import SpeedManager as L25SpeedManager
from linkerbot.hand.l30.current import (
    milliamps_to_protocol_currents,
    protocol_currents_to_milliamps,
)
from linkerbot.hand.l30.torque import (
    milliamps_to_protocol_current_limits as l30_ma_to_current_limits,
)
from linkerbot.hand.l30.torque import (
    protocol_current_limits_to_milliamps as l30_current_limits_to_ma,
)
from linkerbot.hand.o6.angle import AngleManager as O6AngleManager
from linkerbot.hand.o6.speed import O6Speed
from linkerbot.hand.o6.speed import SpeedManager as O6SpeedManager
from linkerbot.hand.o6.torque import O6Torque
from linkerbot.hand.o20.speed import protocol_speeds_to_rpm, rpm_to_protocol_speeds
from linkerbot.hand.o20.torque import (
    milliamps_to_protocol_current_limits as o20_ma_to_current_limits,
)
from linkerbot.hand.o20.torque import (
    protocol_current_limits_to_milliamps as o20_current_limits_to_ma,
)
from linkerbot_hand import CommandKind, HandCommand, SensorType, get_model_capabilities
from linkerbot_hand.backends.legacy.normalization import protocol_vector_values
from linkerbot_hand.commands.validation import validate_command
from linkerbot_hand.errors import TransportError, ValidationError
from linkerbot_transport.can import CANMessageDispatcher


class _Dispatcher:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def send(self, message) -> None:
        self.sent.append(message)

    def subscribe(self, _callback) -> None:
        pass


def test_model_capabilities_declare_protocol_units_and_encodings() -> None:
    l6 = get_model_capabilities("L6")
    o6 = get_model_capabilities("O6")
    o20 = get_model_capabilities("O20")
    l30 = get_model_capabilities("L30")

    l6_speed = l6.joints[0].speed_range
    l6_torque = l6.joints[0].torque_range
    l6_torque_sensor = l6.sensor(SensorType.TORQUES)
    assert l6_speed is not None
    assert l6_torque is not None
    assert l6_torque_sensor is not None
    assert l6_speed.unit == "protocol_raw"
    assert l6_torque.unit == "protocol_raw"
    assert l6.sensor(SensorType.SPEEDS) is None
    assert l6_torque_sensor.unit == "protocol_raw"
    assert l6_torque_sensor.encoding == "u8"

    o6_speed = o6.joints[0].speed_range
    o6_torque = o6.joints[0].torque_range
    o6_speed_sensor = o6.sensor(SensorType.SPEEDS)
    o6_acceleration_sensor = o6.sensor(SensorType.ACCELERATION)
    assert o6_speed is not None
    assert o6_torque is not None
    assert o6_speed_sensor is not None
    assert o6_acceleration_sensor is not None
    assert o6_speed.maximum == 255
    assert o6_speed.unit == "protocol_raw"
    assert o6_torque.maximum == 255
    assert o6_torque.unit == "protocol_raw"
    assert o6_speed_sensor.unit == "protocol_raw"
    assert o6_acceleration_sensor.unit == "protocol_raw"

    o20_speed = o20.joints[0].speed_range
    o20_speed_sensor = o20.sensor(SensorType.SPEEDS)
    l30_torque = l30.joints[0].torque_range
    l30_current_sensor = l30.sensor(SensorType.CURRENT)
    assert o20_speed is not None
    assert o20_speed_sensor is not None
    assert l30_torque is not None
    assert l30_current_sensor is not None
    assert o20_speed.unit == "protocol_unit"
    assert o20_speed_sensor.unit == "protocol_speed"
    assert o20_speed_sensor.encoding == "i16_le"
    assert l30_torque.minimum == 60
    assert l30_torque.maximum == 800
    assert l30_torque.unit == "protocol_current_limit"
    assert l30_torque.integer_only is True
    assert l30_current_sensor.unit == "protocol_current"
    assert l30_current_sensor.encoding == "i16_le"


def test_sensor_capabilities_declare_known_protocol_ranges() -> None:
    for model in ("L6", "O6", "L20Lite", "L25", "L30", "O20", "O30i"):
        capabilities = get_model_capabilities(model)
        for sensor in capabilities.sensors:
            assert sensor.unit is not None
            assert sensor.encoding is not None
            if model in {"L30", "O20"} and sensor.sensor is SensorType.ANGLES:
                assert sensor.value_range is None
                continue
            assert sensor.value_range is not None
            assert sensor.value_range.unit == sensor.unit
            assert sensor.value_range.integer_only

    o30i = get_model_capabilities("O30i")
    assert all(
        joint.raw_angle_range is not None
        and joint.raw_angle_range.unit == "protocol_raw_u8"
        and joint.speed_range is not None
        and joint.speed_range.unit == "protocol_raw_u8"
        and joint.torque_range is not None
        and joint.torque_range.unit == "protocol_raw_u8"
        for joint in o30i.joints
    )


def test_classic_physical_helpers_are_explicit_conversions() -> None:
    assert L6Torque.from_raw([255] * 6).to_milliamps() == [1400.0] * 6
    assert L6Torque.from_milliamps([1400.0] * 6).to_raw() == [255] * 6
    assert O6Torque.from_raw([255] * 6).to_milliamps() == [1000.0] * 6
    assert O6Torque.from_milliamps([1000.0] * 6).to_raw() == [255] * 6
    assert O6Speed.from_raw([255] * 6).to_rpm() == [180.0] * 6
    assert O6Speed.from_rpm([180.0] * 6).to_raw() == [255] * 6


def test_can_fd_physical_helpers_are_explicit_conversions() -> None:
    assert protocol_speeds_to_rpm((-100,) * 16) == (-73.2,) * 16
    assert rpm_to_protocol_speeds((-73.2,) * 16) == (-100,) * 16
    assert protocol_currents_to_milliamps((100,) * 17) == (650.0,) * 17
    assert milliamps_to_protocol_currents((650.0,) * 17) == (100,) * 17
    assert l30_current_limits_to_ma((100,) * 17) == (650.0,) * 17
    assert l30_ma_to_current_limits((650.0,) * 17) == (100,) * 17
    assert o20_current_limits_to_ma((100,) * 16) == (650.0,) * 16
    assert o20_ma_to_current_limits((650.0,) * 16) == (100,) * 16


def test_l6_speed_snapshot_is_protocol_target_not_measured_feedback() -> None:
    dispatcher = _Dispatcher()
    manager = L6SpeedManager(0x28, cast(CANMessageDispatcher, dispatcher))

    assert manager.get_snapshot() is None
    assert not hasattr(manager, "get_blocking")

    manager.set_speeds([128] * 6)

    snapshot = manager.get_snapshot()
    assert snapshot is not None
    assert snapshot.speeds == (128,) * 6
    assert list(dispatcher.sent[0].data) == [0x05, 128, 128, 128, 128, 128, 128]


@pytest.mark.parametrize(
    ("manager_type", "values", "expected_frames"),
    [
        (L6AngleManager, list(range(6)), [[0x01, *range(6)]]),
        (L6SpeedManager, list(range(6)), [[0x05, *range(6)]]),
        (O6AngleManager, list(range(6)), [[0x01, *range(6)]]),
        (O6SpeedManager, list(range(6)), [[0x05, *range(6)]]),
        (
            L20LiteAngleManager,
            list(range(10)),
            [[0x01, *range(6)], [0x04, *range(6, 10)]],
        ),
        (
            L20LiteSpeedManager,
            list(range(10)),
            [[0x05, *range(5)], [0x06, *range(5, 10)]],
        ),
        (
            L25AngleManager,
            list(range(16)),
            [
                [0x41, 0, 1, 2, 0, 0, 3],
                [0x42, 4, 0, 5, 0, 0, 6],
                [0x43, 7, 0, 8, 0, 0, 9],
                [0x44, 10, 0, 11, 0, 0, 12],
                [0x45, 13, 0, 14, 0, 0, 15],
            ],
        ),
        (
            L25SpeedManager,
            list(range(16)),
            [
                [0x49, 0, 1, 2, 0, 0, 3],
                [0x4A, 4, 0, 5, 0, 0, 6],
                [0x4B, 7, 0, 8, 0, 0, 9],
                [0x4C, 10, 0, 11, 0, 0, 12],
                [0x4D, 13, 0, 14, 0, 0, 15],
            ],
        ),
    ],
)
def test_classic_managers_send_protocol_values_without_conversion(
    manager_type: type[Any], values: list[int], expected_frames: list[list[int]]
) -> None:
    dispatcher = _Dispatcher()
    manager = manager_type(0x28, dispatcher)

    if "Angle" in manager_type.__name__:
        manager.set_angles(values)
    else:
        manager.set_speeds(values)

    assert [list(message.data) for message in dispatcher.sent] == expected_frames


def test_l30_current_limits_accept_only_protocol_integers() -> None:
    capabilities = get_model_capabilities("L30")
    command = HandCommand(CommandKind.TORQUES, (100,) * 17)

    assert validate_command(command, capabilities) == command
    with pytest.raises(ValidationError, match=r"torque\[0\] must be int"):
        validate_command(HandCommand(CommandKind.TORQUES, (100.0,) * 17), capabilities)


def test_sensor_normalization_returns_only_protocol_values() -> None:
    assert (
        protocol_vector_values(
            (128,) * 6,
            sensor=SensorType.SPEEDS,
            model="O6",
        )
        == (128,) * 6
    )

    assert protocol_vector_values(
        (0, 1, 2, 100, 200, 254),
        sensor=SensorType.ACCELERATION,
        model="O6",
    ) == (0, 1, 2, 100, 200, 254)

    assert protocol_vector_values(
        (60, 100, 800),
        sensor=SensorType.CURRENT,
        model="L30",
    ) == (60, 100, 800)


def test_protocol_sensor_normalization_rejects_conversion_only_value_objects() -> None:
    speed = O6Speed.from_raw([128] * 6)

    with pytest.raises(TransportError, match="no explicit protocol sequence"):
        protocol_vector_values(
            speed,
            sensor=SensorType.SPEEDS,
            model="O6",
        )
