from types import SimpleNamespace

import pytest

from linkerbot.hand.l6.speed import SpeedManager as L6SpeedManager
from linkerbot.hand.l6.torque import L6Torque
from linkerbot.hand.o6.speed import O6Acceleration, O6Speed
from linkerbot.hand.o6.torque import O6Torque
from linkerbot_hand import CommandKind, HandCommand, SensorType, get_model_capabilities
from linkerbot_hand.backends.legacy.normalization import (
    command_values_for_legacy,
    normalize_vector_sensor,
)
from linkerbot_hand.commands.validation import validate_command


class _Dispatcher:
    def __init__(self) -> None:
        self.sent = []

    def send(self, message) -> None:
        self.sent.append(message)


def test_model_capabilities_declare_public_command_and_feedback_units() -> None:
    l6 = get_model_capabilities("L6")
    o6 = get_model_capabilities("O6")
    o20 = get_model_capabilities("O20")
    l30 = get_model_capabilities("L30")

    assert l6.joints[0].speed_range.unit == "percent"
    assert l6.joints[0].torque_range.unit == "mA"
    assert l6.sensor(SensorType.SPEEDS) is None
    assert l6.sensor(SensorType.TORQUES).unit == "mA"

    assert o6.joints[0].speed_range.maximum == 180
    assert o6.joints[0].speed_range.unit == "rpm"
    assert o6.joints[0].torque_range.maximum == 1000
    assert o6.joints[0].torque_range.unit == "mA"
    assert o6.sensor(SensorType.SPEEDS).unit == "rpm"
    assert o6.sensor(SensorType.ACCELERATION).unit == "degree_per_second_squared"

    assert o20.joints[0].speed_range.unit == "protocol_unit"
    assert o20.sensor(SensorType.SPEEDS).unit == "rpm"
    assert l30.joints[0].torque_range.minimum == 390
    assert l30.joints[0].torque_range.maximum == 5200
    assert l30.joints[0].torque_range.unit == "mA"
    assert l30.joints[0].torque_range.integer_only is False
    assert l30.sensor(SensorType.CURRENT).unit == "mA"


def test_classic_physical_helpers_follow_documented_raw_conversions() -> None:
    assert L6Torque.from_raw([255] * 6).to_milliamps() == [1400.0] * 6
    assert L6Torque.from_milliamps([1400.0] * 6).to_raw() == [255] * 6
    assert O6Torque.from_raw([255] * 6).to_milliamps() == [1000.0] * 6
    assert O6Torque.from_milliamps([1000.0] * 6).to_raw() == [255] * 6
    assert O6Speed.from_raw([255] * 6).to_rpm() == [180.0] * 6
    assert O6Speed.from_rpm([180.0] * 6).to_raw() == [255] * 6


def test_l6_speed_snapshot_is_last_target_and_never_measured_feedback() -> None:
    dispatcher = _Dispatcher()
    manager = L6SpeedManager(0x28, dispatcher)

    assert manager.get_snapshot() is None
    assert not hasattr(manager, "get_blocking")

    manager.set_speeds([50.0] * 6)

    snapshot = manager.get_snapshot()
    assert snapshot is not None
    assert snapshot.speeds.to_list() == [50.0] * 6
    assert list(dispatcher.sent[0].data) == [0x05, 128, 128, 128, 128, 128, 128]


@pytest.mark.parametrize(
    ("public_value", "raw_value"),
    ((650.0, 100), (390.0, 60)),
)
def test_l30_current_limits_convert_to_protocol_values(
    public_value: float,
    raw_value: int,
) -> None:
    assert command_values_for_legacy("L30", CommandKind.TORQUES, [public_value]) == [
        raw_value
    ]


def test_l30_current_limit_accepts_float_milliamps_before_quantization() -> None:
    command = HandCommand(CommandKind.TORQUES, (650.25,) * 17)

    assert validate_command(command, get_model_capabilities("L30")) == command
    assert (
        command_values_for_legacy("L30", CommandKind.TORQUES, list(command.values))
        == [100] * 17
    )


def test_sensor_normalization_returns_physical_and_raw_values() -> None:
    speed = O6Speed.from_raw([128] * 6)
    values, raw_values = normalize_vector_sensor(
        SimpleNamespace(speeds=speed),
        speed,
        sensor=SensorType.SPEEDS,
        model="O6",
    )
    assert raw_values == (128,) * 6
    assert values == pytest.approx((128 * 180 / 255,) * 6)

    acceleration = O6Acceleration.from_raw([0, 1, 2, 100, 200, 254])
    values, raw_values = normalize_vector_sensor(
        SimpleNamespace(accelerations=acceleration),
        acceleration,
        sensor=SensorType.ACCELERATION,
        model="O6",
    )
    assert raw_values == (0, 1, 2, 100, 200, 254)
    assert values == pytest.approx((254 * 8.7, 8.7, 17.4, 870, 1740, 254 * 8.7))

    values, raw_values = normalize_vector_sensor(
        SimpleNamespace(currents=(60, 100, 800)),
        (60, 100, 800),
        sensor=SensorType.CURRENT,
        model="L30",
    )
    assert raw_values == (60, 100, 800)
    assert values == pytest.approx((390.0, 650.0, 5200.0))
