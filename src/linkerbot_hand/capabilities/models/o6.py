"""Declared O6 capability profile available before device identification."""

from ...api.enums import Confidence, HandSide, JointMotionType, TransportKind
from ..ranges import NumericRange
from ..types import (
    CommandSemantic,
    DeviceCapabilities,
    OperationCapabilities,
    TelemetrySemantic,
)
from ._common import (
    U8_RANGE,
    angle_command,
    angle_telemetry,
    command,
    identity,
    joint,
    telemetry,
    transport,
)

_JOINT_NAMES = ("thumb_flex", "thumb_abd", "index", "middle", "ring", "pinky")
_FINGERS = ("thumb", "index", "middle", "ring", "pinky")
_ACCELERATION_RANGE = NumericRange(0, 254, integer_only=True, step=1)


def declared_capabilities(side: HandSide) -> DeviceCapabilities:
    """Return immutable, unverified O6 metadata for ``side``."""

    joints = tuple(
        joint(
            index,
            name,
            JointMotionType.ABDUCTION if index == 1 else JointMotionType.FLEXION,
            U8_RANGE,
            raw_at_zero=0,
            raw_at_hundred=255,
            zero_label="toward_thumb_web" if index == 1 else "flexed",
            hundred_label="away_from_thumb_web" if index == 1 else "extended",
        )
        for index, name in enumerate(_JOINT_NAMES)
    )
    return DeviceCapabilities(
        identity=identity(
            "O6",
            side,
            supported_fields=(
                "serial_number",
                "pcb_version",
                "firmware_version",
                "mechanical_version",
                "product_code",
                "sensor_variant",
                "side",
            ),
        ),
        joints=joints,
        commands=(
            angle_command(joints, "u8"),
            command(
                CommandSemantic.SPEEDS,
                _JOINT_NAMES,
                U8_RANGE,
                "u8",
                raw_unit="count",
                raw_scale_note="documentation states value * 180 / 255",
                unit_confidence=Confidence.CONFLICTING,
            ),
            command(
                CommandSemantic.ACCELERATIONS,
                _JOINT_NAMES,
                _ACCELERATION_RANGE,
                "u8",
                raw_unit="count",
                raw_scale_note="zero is a special maximum-acceleration encoding",
                unit_confidence=Confidence.CONFLICTING,
            ),
            command(
                CommandSemantic.CURRENT_LIMITS,
                _JOINT_NAMES,
                U8_RANGE,
                "u8",
                raw_unit="count",
                raw_scale_note="source title and value * 1000 / 255 formula conflict",
                unit_confidence=Confidence.CONFLICTING,
            ),
        ),
        telemetry=(
            angle_telemetry(
                joints,
                "u8",
                recommended_hz=60,
                estimated_transaction_s=0.005,
                verified_max_hz=200,
            ),
            telemetry(
                TelemetrySemantic.CONFIGURED_SPEEDS,
                _JOINT_NAMES,
                "u8",
                shape=(6,),
                raw_range=U8_RANGE,
                raw_unit="count",
                raw_scale_note="configuration response; not measured joint speed",
                unit_confidence=Confidence.CONFLICTING,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.005,
                device_measured=False,
            ),
            telemetry(
                TelemetrySemantic.CONFIGURED_ACCELERATIONS,
                _JOINT_NAMES,
                "u8",
                shape=(6,),
                raw_range=_ACCELERATION_RANGE,
                raw_unit="count",
                raw_scale_note="configuration response with zero special encoding",
                unit_confidence=Confidence.CONFLICTING,
                recommended_hz=10,
                stale_after_s=0.3,
                estimated_transaction_s=0.005,
                device_measured=False,
            ),
            telemetry(
                TelemetrySemantic.CURRENT_LIMITS,
                _JOINT_NAMES,
                "u8",
                shape=(6,),
                raw_range=U8_RANGE,
                raw_unit="count",
                raw_scale_note="configuration response; source unit is conflicting",
                unit_confidence=Confidence.CONFLICTING,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.005,
                device_measured=False,
            ),
            telemetry(
                TelemetrySemantic.FORCE,
                _FINGERS,
                "u8",
                shape=(5, 10, 4),
                raw_range=U8_RANGE,
                raw_unit="count",
                raw_scale_note="available only after selecting a compatible sensor profile",
                unit_confidence=Confidence.MEASURED,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.012,
            ),
            telemetry(
                TelemetrySemantic.TEMPERATURES,
                _JOINT_NAMES,
                "u8",
                shape=(6,),
                raw_range=U8_RANGE,
                raw_unit="degree_celsius",
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=1,
                stale_after_s=3,
                estimated_transaction_s=0.005,
            ),
            telemetry(
                TelemetrySemantic.FAULTS,
                _JOINT_NAMES,
                "u8_bitmask",
                shape=(6,),
                raw_range=U8_RANGE,
                raw_unit=None,
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=5,
                stale_after_s=0.5,
                estimated_transaction_s=0.005,
            ),
        ),
        stall_protection=None,
        transport=transport(TransportKind.CAN),
        operations=OperationCapabilities(),
    )


__all__ = ["declared_capabilities"]
