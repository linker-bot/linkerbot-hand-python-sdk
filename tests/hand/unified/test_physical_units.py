from typing import Any, cast

import pytest

from linkerbot.hand.l6.angle import AngleManager as L6AngleManager
from linkerbot.hand.l6.fault import L6Fault
from linkerbot.hand.l6.speed import SpeedManager as L6SpeedManager
from linkerbot.hand.l6.temperature import L6Temperature
from linkerbot.hand.l6.torque import L6Torque
from linkerbot.hand.l20lite.angle import AngleManager as L20LiteAngleManager
from linkerbot.hand.l20lite.fault import L20liteFault
from linkerbot.hand.l20lite.speed import SpeedManager as L20LiteSpeedManager
from linkerbot.hand.l20lite.temperature import L20liteTemperature
from linkerbot.hand.l25.angle import AngleManager as L25AngleManager
from linkerbot.hand.l25.speed import SpeedManager as L25SpeedManager
from linkerbot.hand.l25.temperature import L25Temperature
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
from linkerbot.hand.o6.fault import O6Fault
from linkerbot.hand.o6.speed import O6Speed
from linkerbot.hand.o6.speed import SpeedManager as O6SpeedManager
from linkerbot.hand.o6.temperature import O6Temperature
from linkerbot.hand.o6.torque import O6Torque
from linkerbot.hand.o20.speed import protocol_speeds_to_rpm, rpm_to_protocol_speeds
from linkerbot.hand.o20.torque import (
    milliamps_to_protocol_current_limits as o20_ma_to_current_limits,
)
from linkerbot.hand.o20.torque import (
    protocol_current_limits_to_milliamps as o20_current_limits_to_ma,
)
from linkerbot_hand import (
    CommandKind,
    HandCommand,
    SensorType,
    SensorValueKind,
    get_model_capabilities,
)
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


_EXPECTED_CLASSIC_SENSOR_METADATA = {
    "L6": {
        SensorType.ANGLES: ("count", "u8", None, None, None, "numeric", None),
        SensorType.TORQUES: (
            "count",
            "u8",
            "mA",
            1400 / 255,
            0,
            "numeric",
            None,
        ),
        SensorType.FORCE: (
            "count",
            "u8",
            None,
            None,
            None,
            "tactile_matrix",
            (12, 6),
        ),
        SensorType.TEMPERATURE: (
            "degree_celsius",
            "u8",
            "degree_celsius",
            1,
            0,
            "numeric",
            None,
        ),
        SensorType.CURRENT: (
            "count",
            "u8",
            "mA",
            1400 / 255,
            0,
            "numeric",
            None,
        ),
        SensorType.FAULTS: (
            "dimensionless",
            "u8",
            None,
            None,
            None,
            "bitmask",
            None,
        ),
    },
    "O6": {
        SensorType.ANGLES: ("count", "u8", None, None, None, "numeric", None),
        SensorType.SPEEDS: ("count", "u8", "rpm", None, None, "numeric", None),
        SensorType.ACCELERATION: (
            "count",
            "u8",
            "degree_per_second_squared",
            None,
            None,
            "numeric",
            None,
        ),
        SensorType.TORQUES: ("count", "u8", "mA", None, None, "numeric", None),
        SensorType.FORCE: (
            "count",
            "u8",
            None,
            None,
            None,
            "tactile_matrix",
            (10, 4),
        ),
        SensorType.TEMPERATURE: (
            "degree_celsius",
            "u8",
            "degree_celsius",
            1,
            0,
            "numeric",
            None,
        ),
        SensorType.FAULTS: (
            "dimensionless",
            "u8",
            None,
            None,
            None,
            "bitmask",
            None,
        ),
    },
    "L20Lite": {
        SensorType.ANGLES: ("count", "u8", None, None, None, "numeric", None),
        SensorType.SPEEDS: ("count", "u8", None, None, None, "numeric", None),
        SensorType.TORQUES: ("count", "u8", None, None, None, "numeric", None),
        SensorType.FORCE: (
            "count",
            "u8",
            None,
            None,
            None,
            "tactile_matrix",
            (12, 6),
        ),
        SensorType.TEMPERATURE: (
            "degree_celsius",
            "u8",
            "degree_celsius",
            1,
            0,
            "numeric",
            None,
        ),
        SensorType.FAULTS: (
            "dimensionless",
            "u8",
            None,
            None,
            None,
            "bitmask",
            None,
        ),
    },
    "L25": {
        SensorType.ANGLES: ("count", "u8", "degree", None, None, "numeric", None),
        SensorType.SPEEDS: ("count", "u8", None, None, None, "numeric", None),
        SensorType.TORQUES: ("count", "u8", "mA", None, None, "numeric", None),
        SensorType.FORCE: (
            "count",
            "u8",
            None,
            None,
            None,
            "tactile_matrix",
            (12, 6),
        ),
        SensorType.TEMPERATURE: (
            "degree_celsius",
            "u8",
            "degree_celsius",
            1,
            0,
            "numeric",
            None,
        ),
        SensorType.FAULTS: (
            "dimensionless",
            "u8",
            None,
            None,
            None,
            "bitmask",
            None,
        ),
    },
}

_EXPECTED_CAN_FD_SENSOR_METADATA = {
    "L30": {
        SensorType.ANGLES: ("count", "i16_be", None, None, None, "numeric", None),
        SensorType.SPEEDS: (
            "count",
            "i16_be",
            "rpm",
            0.732,
            0,
            "numeric",
            None,
        ),
        SensorType.FORCE: (
            "count",
            "u8",
            None,
            None,
            None,
            "tactile_matrix",
            (12, 6),
        ),
        SensorType.TEMPERATURE: (
            "degree_celsius",
            "u8",
            "degree_celsius",
            1,
            0,
            "numeric",
            None,
        ),
        SensorType.CURRENT: (
            "count",
            "i16_be",
            "mA",
            6.5,
            0,
            "numeric",
            None,
        ),
        SensorType.FAULTS: (
            "dimensionless",
            "u8",
            None,
            None,
            None,
            "bitmask",
            None,
        ),
    },
    "O20": {
        SensorType.ANGLES: (
            "count",
            "i16_le",
            "degree",
            None,
            None,
            "numeric",
            None,
        ),
        SensorType.SPEEDS: (
            "count",
            "i16_le",
            "rpm",
            0.732,
            0,
            "numeric",
            None,
        ),
        SensorType.TORQUES: (
            "count",
            "u16_le",
            None,
            None,
            None,
            "numeric",
            None,
        ),
        SensorType.FORCE: (
            "count",
            "u8",
            None,
            None,
            None,
            "tactile_matrix",
            (12, 6),
        ),
        SensorType.TEMPERATURE: (
            "count",
            "u8",
            None,
            None,
            None,
            "numeric",
            None,
        ),
        SensorType.CURRENT: ("mA", "i16_le", "mA", 1, 0, "numeric", None),
        SensorType.FAULTS: (
            "dimensionless",
            "u8",
            None,
            None,
            None,
            "enum",
            None,
        ),
    },
    "O30i": {
        SensorType.ANGLES: ("count", "u8", None, None, None, "numeric", None),
        SensorType.SPEEDS: ("count", "u8", None, None, None, "numeric", None),
        SensorType.ACCELERATION: (
            "count",
            "u8",
            None,
            None,
            None,
            "numeric",
            None,
        ),
        SensorType.TORQUES: ("count", "u8", None, None, None, "numeric", None),
        SensorType.TEMPERATURE: (
            "count",
            "u8",
            None,
            None,
            None,
            "numeric",
            None,
        ),
        SensorType.CURRENT: ("count", "u8", None, None, None, "numeric", None),
        SensorType.FAULTS: (
            "dimensionless",
            "u8",
            None,
            None,
            None,
            "bitmask",
            None,
        ),
        SensorType.VOLTAGE: ("count", "u8", None, None, None, "numeric", None),
        SensorType.MOTION_TIME: (
            "count",
            "u8",
            "millisecond",
            10,
            0,
            "numeric",
            None,
        ),
    },
}


def _assert_sensor_metadata(
    model: str,
    expected: dict[SensorType, tuple[object, ...]],
) -> None:
    capabilities = get_model_capabilities(model)

    assert {sensor.sensor for sensor in capabilities.sensors} == set(expected)
    for sensor_type, metadata in expected.items():
        sensor = capabilities.sensor(sensor_type)
        assert sensor is not None
        assert isinstance(sensor.value_kind, SensorValueKind)
        assert (
            sensor.unit,
            sensor.encoding,
            sensor.physical_unit,
            sensor.scale,
            sensor.offset,
            sensor.value_kind.value,
            sensor.matrix_shape,
        ) == metadata
        if sensor_type is SensorType.ANGLES and model in {"L30", "O20"}:
            assert sensor.value_range is None
        else:
            assert sensor.value_range is not None
            assert sensor.value_range.unit == sensor.unit
            assert sensor.value_range.integer_only


@pytest.mark.parametrize(
    ("model", "expected"), _EXPECTED_CLASSIC_SENSOR_METADATA.items()
)
def test_classic_capabilities_declare_protocol_value_metadata(
    model: str,
    expected: dict[SensorType, tuple[object, ...]],
) -> None:
    _assert_sensor_metadata(model, expected)


@pytest.mark.parametrize(
    ("model", "expected"), _EXPECTED_CAN_FD_SENSOR_METADATA.items()
)
def test_can_fd_capabilities_declare_protocol_value_metadata(
    model: str,
    expected: dict[SensorType, tuple[object, ...]],
) -> None:
    _assert_sensor_metadata(model, expected)


def test_classic_joint_ranges_declare_known_physical_metadata() -> None:
    l6_torque = get_model_capabilities("L6").joints[0].torque_range
    o6_speed = get_model_capabilities("O6").joints[0].speed_range
    l25_thumb_speed = get_model_capabilities("L25").joints[0].speed_range
    l25_index_root_speed = get_model_capabilities("L25").joints[5].speed_range

    assert l6_torque is not None
    assert (l6_torque.physical_unit, l6_torque.scale, l6_torque.offset) == (
        "mA",
        1400 / 255,
        0,
    )
    assert o6_speed is not None
    assert (
        o6_speed.physical_unit,
        o6_speed.physical_minimum,
        o6_speed.physical_maximum,
        o6_speed.scale,
    ) == ("rpm", 0, 180, None)
    assert l25_thumb_speed is not None
    assert (
        l25_thumb_speed.physical_unit,
        l25_thumb_speed.physical_minimum,
        l25_thumb_speed.physical_maximum,
        l25_thumb_speed.scale,
    ) == ("rpm", 0, 180, None)
    assert l25_index_root_speed is not None
    assert l25_index_root_speed.physical_unit is None


def test_can_fd_joint_ranges_do_not_invent_physical_scales() -> None:
    l30_torque = get_model_capabilities("L30").joints[0].torque_range
    o20_angle = get_model_capabilities("O20").joints[0].raw_angle_range
    o20_torque = get_model_capabilities("O20").joints[0].torque_range

    assert l30_torque is not None
    assert (l30_torque.minimum, l30_torque.maximum, l30_torque.scale) == (
        60,
        800,
        6.5,
    )
    assert o20_angle is not None
    assert (
        o20_angle.physical_unit,
        o20_angle.physical_minimum,
        o20_angle.physical_maximum,
        o20_angle.scale,
    ) == ("degree", None, None, None)
    assert o20_torque is not None
    assert (o20_torque.minimum, o20_torque.maximum, o20_torque.scale) == (
        0,
        1000,
        6.5,
    )


def test_o30i_joint_ranges_publish_counts_without_unverified_scales() -> None:
    o30i = get_model_capabilities("O30i")
    for joint in o30i.joints:
        for value_range in (
            joint.raw_angle_range,
            joint.speed_range,
            joint.torque_range,
        ):
            assert value_range is not None
            assert (
                value_range.minimum,
                value_range.maximum,
                value_range.unit,
                value_range.integer_only,
                value_range.step,
                value_range.physical_unit,
                value_range.scale,
                value_range.offset,
            ) == (0, 255, "count", True, 1, None, None, None)


def test_o30i_motion_time_publishes_only_verified_physical_conversion() -> None:
    motion_time = get_model_capabilities("O30i").sensor(SensorType.MOTION_TIME)

    assert motion_time is not None
    assert motion_time.value_range is not None
    assert (
        motion_time.value_range.minimum,
        motion_time.value_range.maximum,
        motion_time.value_range.physical_unit,
        motion_time.value_range.physical_minimum,
        motion_time.value_range.physical_maximum,
        motion_time.value_range.scale,
        motion_time.value_range.offset,
    ) == (0, 255, "millisecond", 0, 2550, 10, 0)


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


@pytest.mark.parametrize(
    ("factory", "raw_values"),
    [
        (L6Fault, [0x80] * 6),
        (O6Fault, [0xD0] * 6),
        (L20liteFault, [0xD0] * 10),
    ],
)
def test_classic_fault_decoders_preserve_reserved_protocol_bits(
    factory: type[Any], raw_values: list[int]
) -> None:
    faults = factory.from_raw(raw_values)

    assert [int(code.value) for code in faults.to_list()] == raw_values
    assert all(not code.has_fault() for code in faults.to_list())


@pytest.mark.parametrize(
    ("factory", "count"),
    [
        (L6Temperature, 6),
        (O6Temperature, 6),
        (L20liteTemperature, 10),
        (L25Temperature, 16),
    ],
)
def test_classic_temperature_decoders_preserve_integer_protocol_values(
    factory: type[Any], count: int
) -> None:
    values = list(range(20, 20 + count))

    temperatures = factory.from_raw(values)

    assert temperatures.to_list() == values
    assert all(type(value) is int for value in temperatures.to_list())


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
