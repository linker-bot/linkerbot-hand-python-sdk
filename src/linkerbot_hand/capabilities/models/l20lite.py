"""Declared L20Lite capability profile."""

from collections.abc import Iterator

from ...api.enums import (
    Confidence,
    HandSide,
    JointMotionType,
    SampleProjection,
    TransportKind,
)
from ..types import (
    CommandSemantic,
    DeviceCapabilities,
    OperationCapabilities,
    TelemetryCapability,
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
from .l20lite_stall import stall_capability, stall_commands, stall_telemetry

_JOINT_SPECS = (
    ("thumb_flex", JointMotionType.FLEXION, "flexed", "extended"),
    (
        "thumb_roll",
        JointMotionType.ROLL,
        "perpendicular_to_palm",
        "parallel_to_palm",
    ),
    ("index_flex", JointMotionType.FLEXION, "flexed", "extended"),
    ("middle_flex", JointMotionType.FLEXION, "flexed", "extended"),
    ("ring_flex", JointMotionType.FLEXION, "flexed", "extended"),
    ("pinky_flex", JointMotionType.FLEXION, "flexed", "extended"),
    (
        "index_abd",
        JointMotionType.ABDUCTION,
        "toward_middle_finger",
        "away_from_middle_finger",
    ),
    (
        "ring_abd",
        JointMotionType.ABDUCTION,
        "toward_middle_finger",
        "away_from_middle_finger",
    ),
    (
        "pinky_abd",
        JointMotionType.ABDUCTION,
        "toward_middle_finger",
        "away_from_middle_finger",
    ),
    (
        "thumb_rotation",
        JointMotionType.ROTATION,
        "toward_index_finger",
        "away_from_index_finger",
    ),
)
_FINGERS = ("thumb", "index", "middle", "ring", "pinky")


def _force_scalar_capabilities() -> Iterator[TelemetryCapability]:
    """Category U8 objects retain documented meaning without guessed scaling."""
    for semantic, field, note, confidence in (
        (
            TelemetrySemantic.FORCE_FNS,
            "fns",
            (
                "0x20: five fingertip normal-force scalars; documented span 0..20 N, "
                "raw-to-N scale unverified. Not a sum of B1..B5 matrices."
            ),
            Confidence.UNKNOWN,
        ),
        (
            TelemetrySemantic.FORCE_FTS,
            "fts",
            (
                "0x21: five fingertip tangential-force scalars; documented span 0..20 N, "
                "raw-to-N scale unverified. No per-point Ft object."
            ),
            Confidence.UNKNOWN,
        ),
        (
            TelemetrySemantic.FORCE_FDS,
            "fds",
            (
                "0x22: direction codes 0..127; 255 means undefined, 128..254 reserved. "
                "Text says 0..359 degrees but formula 2.834645669291339/code implies "
                "0..360; no conversion. Top=0, right=90, bottom=180, left=270 degrees."
            ),
            Confidence.CONFLICTING,
        ),
        (
            TelemetrySemantic.FORCE_PX,
            "px",
            "0x23: five raw proximity values; no verified distance scale or boolean threshold.",
            Confidence.UNKNOWN,
        ),
    ):
        yield telemetry(
            semantic,
            _FINGERS,
            "u8",
            shape=(5,),
            raw_range=U8_RANGE,
            raw_unit="count",
            raw_scale_note=note,
            unit_confidence=confidence,
            recommended_hz=30,
            stale_after_s=0.1,
            estimated_transaction_s=0.002,
            force_fields=(field,),
        )


def declared_capabilities(side: HandSide) -> DeviceCapabilities:
    """Return immutable, unverified L20Lite metadata for ``side``."""

    joints = tuple(
        joint(
            index,
            name,
            motion_type,
            U8_RANGE,
            raw_at_zero=0,
            raw_at_hundred=255,
            zero_label=zero_label,
            hundred_label=hundred_label,
        )
        for index, (name, motion_type, zero_label, hundred_label) in enumerate(
            _JOINT_SPECS
        )
    )
    names = tuple(value.name for value in joints)
    stall = stall_capability(names)
    return DeviceCapabilities(
        identity=identity(
            "L20Lite",
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
            *stall_commands(stall),
            angle_command(joints, "u8", returns_current_state=True),
            command(
                CommandSemantic.SPEEDS,
                names,
                U8_RANGE,
                "u8",
                raw_unit="count",
                raw_scale_note="raw speed setting; physical scale is unverified",
                unit_confidence=Confidence.UNKNOWN,
            ),
            command(
                CommandSemantic.EFFORT_LIMITS,
                names,
                U8_RANGE,
                "u8",
                raw_unit=None,
                raw_scale_note=(
                    "raw limit setting; physical quantity and scale are unverified"
                ),
                unit_confidence=Confidence.UNKNOWN,
            ),
        ),
        telemetry=(
            *stall_telemetry(stall),
            angle_telemetry(
                joints,
                "u8",
                recommended_hz=60,
                estimated_transaction_s=0.006,
            ),
            telemetry(
                TelemetrySemantic.MEASURED_SPEEDS,
                names,
                "u8",
                shape=(10,),
                raw_range=U8_RANGE,
                raw_unit="count",
                raw_scale_note="real-time speed; physical scale is unverified",
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.006,
            ),
            telemetry(
                TelemetrySemantic.EFFORT_LIMITS,
                names,
                "u8",
                shape=(10,),
                raw_range=U8_RANGE,
                raw_unit=None,
                raw_scale_note=(
                    "a bare query returns the real-time value; the protocol "
                    "text alternates between current and torque, so the exact "
                    "physical quantity awaits hardware confirmation"
                ),
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.006,
            ),
            telemetry(
                TelemetrySemantic.FORCE,
                _FINGERS,
                "u8",
                shape=(5, 12, 6),
                raw_range=U8_RANGE,
                raw_unit="count",
                raw_scale_note=(
                    "raw tactile sensor count; physical calibration unknown; "
                    "scalar force object units do not apply to this matrix"
                ),
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.02,
                payload_projection=SampleProjection.TACTILE_MATRIX,
            ),
            *_force_scalar_capabilities(),
            telemetry(
                TelemetrySemantic.TEMPERATURES,
                names,
                "u8",
                shape=(10,),
                raw_range=U8_RANGE,
                raw_unit="degree_celsius",
                raw_scale_note="1 degree Celsius/count",
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=1,
                stale_after_s=3,
                estimated_transaction_s=0.006,
            ),
            telemetry(
                TelemetrySemantic.FAULTS,
                names,
                "u8_bitmask",
                decoded_statuses=True,
                shape=(10,),
                raw_range=U8_RANGE,
                raw_unit=None,
                raw_scale_note=(
                    "bit5 load overload; bit3 overcurrent; bit2 overtemperature; "
                    "bit1 magnetic encoder; bit0 over/undervoltage; "
                    "bits7/6/4 undefined"
                ),
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=5,
                stale_after_s=0.5,
                estimated_transaction_s=0.006,
            ),
        ),
        stall_protection=stall,
        transport=transport(TransportKind.CAN),
        operations=OperationCapabilities(write_stall_protection=True),
    )


__all__ = ["declared_capabilities"]
