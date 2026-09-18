"""Declared O30 profile exposing only twenty installed protocol slots."""

from ...api.commands import AckSupport
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
    StallFieldCapability,
    StallProtectionCapability,
    TelemetrySemantic,
    TransportCapability,
)
from ._common import (
    U8_RANGE,
    angle_command,
    angle_telemetry,
    command,
    identity,
    joint,
    telemetry,
)

_RAW_ENCODING = "u8_v25_object"
_SENSOR_CHANNELS = ("thumb", "index", "middle", "ring", "pinky")

_JOINT_SPECS = (
    (
        "thumb_rotation",
        JointMotionType.ROTATION,
        "away_from_index_finger",
        "toward_index_finger",
    ),
    (
        "thumb_roll",
        JointMotionType.ROLL,
        "parallel_to_palm",
        "perpendicular_to_palm",
    ),
    ("index_abd", JointMotionType.ABDUCTION, "toward_thumb", "away_from_thumb"),
    ("middle_abd", JointMotionType.ABDUCTION, "toward_thumb", "away_from_thumb"),
    ("ring_abd", JointMotionType.ABDUCTION, "toward_thumb", "away_from_thumb"),
    ("pinky_abd", JointMotionType.ABDUCTION, "toward_thumb", "away_from_thumb"),
    ("thumb_root1", JointMotionType.FLEXION, "flexed", "extended"),
    ("index_root1", JointMotionType.FLEXION, "flexed", "extended"),
    ("middle_root1", JointMotionType.FLEXION, "flexed", "extended"),
    ("ring_root1", JointMotionType.FLEXION, "flexed", "extended"),
    ("pinky_root1", JointMotionType.FLEXION, "flexed", "extended"),
    ("index_root2", JointMotionType.FLEXION, "flexed", "extended"),
    ("middle_root2", JointMotionType.FLEXION, "flexed", "extended"),
    ("ring_root2", JointMotionType.FLEXION, "flexed", "extended"),
    ("pinky_root2", JointMotionType.FLEXION, "flexed", "extended"),
    ("thumb_tip", JointMotionType.FLEXION, "flexed", "extended"),
    ("index_tip", JointMotionType.FLEXION, "flexed", "extended"),
    ("middle_tip", JointMotionType.FLEXION, "flexed", "extended"),
    ("ring_tip", JointMotionType.FLEXION, "flexed", "extended"),
    ("pinky_tip", JointMotionType.FLEXION, "flexed", "extended"),
)


def declared_capabilities(side: HandSide) -> DeviceCapabilities:
    """Return immutable, unverified O30 metadata for ``side``."""

    joints = tuple(
        joint(
            index,
            name,
            motion_type,
            U8_RANGE,
            raw_at_zero=(255 if motion_type is JointMotionType.FLEXION else 0),
            raw_at_hundred=(0 if motion_type is JointMotionType.FLEXION else 255),
            zero_label=zero_label,
            hundred_label=hundred_label,
        )
        for index, (name, motion_type, zero_label, hundred_label) in enumerate(
            _JOINT_SPECS
        )
    )
    names = tuple(value.name for value in joints)
    stall_field = StallFieldCapability(
        raw_range=U8_RANGE,
        raw_encoding=_RAW_ENCODING,
        raw_unit=None,
        raw_scale_note="public 20 channels map to installed slots in V25",
        unit_confidence=Confidence.UNKNOWN,
    )
    stall = StallProtectionCapability(
        channels=names,
        detection_time=stall_field,
        threshold=stall_field,
        hold_current=stall_field,
        physical_semantics_confidence=Confidence.UNKNOWN,
        safety_range_verified=False,
        read_supported=True,
        write_supported=True,
    )
    return DeviceCapabilities(
        identity=identity(
            "O30",
            side,
            supported_fields=(
                "serial_number",
                "pcb_version",
                "adapter_board_version",
                "control_board_version",
                "bootloader_version",
                "firmware_version",
                "mechanical_version",
                "product_code",
                "protocol_name",
                "protocol_version",
                "power_supply_range",
                "microcontroller_uid",
                "build_time",
                "supported_interfaces",
                "reported_side",
            ),
        ),
        joints=joints,
        commands=(
            angle_command(
                joints,
                _RAW_ENCODING,
                ack_support=AckSupport.SUPPORTED,
                returns_current_state=True,
            ),
            command(
                CommandSemantic.SPEEDS,
                names,
                U8_RANGE,
                _RAW_ENCODING,
                raw_unit="count",
                unit_confidence=Confidence.UNKNOWN,
                ack_support=AckSupport.SUPPORTED,
            ),
            command(
                CommandSemantic.ACCELERATIONS,
                names,
                U8_RANGE,
                _RAW_ENCODING,
                raw_unit="count",
                unit_confidence=Confidence.UNKNOWN,
                ack_support=AckSupport.SUPPORTED,
            ),
            command(
                CommandSemantic.EFFORT_LIMITS,
                names,
                U8_RANGE,
                _RAW_ENCODING,
                raw_unit=None,
                unit_confidence=Confidence.UNKNOWN,
                ack_support=AckSupport.SUPPORTED,
            ),
            command(
                CommandSemantic.CURRENT_LIMITS,
                names,
                U8_RANGE,
                _RAW_ENCODING,
                raw_unit="count",
                unit_confidence=Confidence.UNKNOWN,
                ack_support=AckSupport.SUPPORTED,
            ),
            command(
                CommandSemantic.MOTION_TIMES,
                names,
                U8_RANGE,
                _RAW_ENCODING,
                raw_unit="count",
                raw_scale_note="documentation states 10 ms/count; API returns count",
                unit_confidence=Confidence.DOCUMENTED,
                ack_support=AckSupport.SUPPORTED,
            ),
            command(
                CommandSemantic.STALL_DETECTION_TIMES,
                names,
                U8_RANGE,
                _RAW_ENCODING,
                raw_unit=None,
                unit_confidence=Confidence.UNKNOWN,
                ack_support=AckSupport.SUPPORTED,
            ),
            command(
                CommandSemantic.STALL_THRESHOLDS,
                names,
                U8_RANGE,
                _RAW_ENCODING,
                raw_unit=None,
                unit_confidence=Confidence.UNKNOWN,
                ack_support=AckSupport.SUPPORTED,
            ),
            command(
                CommandSemantic.STALL_HOLD_CURRENTS,
                names,
                U8_RANGE,
                _RAW_ENCODING,
                raw_unit=None,
                unit_confidence=Confidence.UNKNOWN,
                ack_support=AckSupport.SUPPORTED,
            ),
        ),
        telemetry=(
            angle_telemetry(
                joints,
                _RAW_ENCODING,
                recommended_hz=30,
                estimated_transaction_s=0.004,
            ),
            telemetry(
                TelemetrySemantic.MEASURED_SPEEDS,
                names,
                _RAW_ENCODING,
                shape=(20,),
                raw_range=U8_RANGE,
                raw_unit="count",
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.004,
            ),
            telemetry(
                TelemetrySemantic.CONFIGURED_SPEEDS,
                names,
                _RAW_ENCODING,
                shape=(20,),
                raw_range=U8_RANGE,
                raw_unit="count",
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.004,
                device_measured=False,
            ),
            telemetry(
                TelemetrySemantic.CONFIGURED_ACCELERATIONS,
                names,
                _RAW_ENCODING,
                shape=(20,),
                raw_range=U8_RANGE,
                raw_unit="count",
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=10,
                stale_after_s=0.3,
                estimated_transaction_s=0.004,
                device_measured=False,
            ),
            telemetry(
                TelemetrySemantic.EFFORT_LIMITS,
                names,
                _RAW_ENCODING,
                shape=(20,),
                raw_range=U8_RANGE,
                raw_unit=None,
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.004,
                device_measured=False,
            ),
            telemetry(
                TelemetrySemantic.TEMPERATURES,
                names,
                _RAW_ENCODING,
                shape=(20,),
                raw_range=U8_RANGE,
                raw_unit="count",
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=1,
                stale_after_s=3,
                estimated_transaction_s=0.004,
            ),
            telemetry(
                TelemetrySemantic.FORCE,
                _SENSOR_CHANNELS,
                "o30_tactile_descriptor",
                shape=(5,),
                raw_range=None,
                raw_unit=None,
                raw_scale_note=(
                    "runtime metadata supplies sensor type, unit, shape, descriptor, "
                    "and exact raw byte stream; no physical conversion"
                ),
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=10,
                stale_after_s=0.3,
                estimated_transaction_s=0.2,
                payload_projection=SampleProjection.TACTILE_DESCRIPTOR,
            ),
            telemetry(
                TelemetrySemantic.CURRENTS,
                names,
                _RAW_ENCODING,
                shape=(20,),
                raw_range=U8_RANGE,
                raw_unit="count",
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=10,
                stale_after_s=0.3,
                estimated_transaction_s=0.004,
            ),
            telemetry(
                TelemetrySemantic.CURRENT_LIMITS,
                names,
                _RAW_ENCODING,
                shape=(20,),
                raw_range=U8_RANGE,
                raw_unit="count",
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.004,
                device_measured=False,
            ),
            telemetry(
                TelemetrySemantic.TORQUES,
                names,
                _RAW_ENCODING,
                shape=(20,),
                raw_range=U8_RANGE,
                raw_unit=None,
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=10,
                stale_after_s=0.3,
                estimated_transaction_s=0.004,
            ),
            telemetry(
                TelemetrySemantic.FAULTS,
                names,
                "u8_bitmask_v25_object",
                shape=(20,),
                raw_range=U8_RANGE,
                raw_unit=None,
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=5,
                stale_after_s=0.5,
                estimated_transaction_s=0.004,
                # ``protocols/o30/telemetry.py`` wires decode_fault_statuses for
                # this object, so every fault sample carries per-joint
                # JointFaultStatus values next to the raw bytes.
                decoded_statuses=True,
            ),
            telemetry(
                TelemetrySemantic.VOLTAGES,
                names,
                _RAW_ENCODING,
                shape=(20,),
                raw_range=U8_RANGE,
                raw_unit="count",
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=1,
                stale_after_s=3,
                estimated_transaction_s=0.004,
            ),
            telemetry(
                TelemetrySemantic.MOTION_TIMES,
                names,
                _RAW_ENCODING,
                shape=(20,),
                raw_range=U8_RANGE,
                raw_unit="count",
                raw_scale_note="documentation states 10 ms/count; API returns count",
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=10,
                stale_after_s=0.3,
                estimated_transaction_s=0.004,
                device_measured=False,
            ),
            *(
                telemetry(
                    semantic,
                    names,
                    _RAW_ENCODING,
                    shape=(20,),
                    raw_range=U8_RANGE,
                    raw_unit=None,
                    unit_confidence=Confidence.UNKNOWN,
                    recommended_hz=1,
                    stale_after_s=3,
                    estimated_transaction_s=0.004,
                    device_measured=False,
                )
                for semantic in (
                    TelemetrySemantic.STALL_DETECTION_TIMES,
                    TelemetrySemantic.STALL_THRESHOLDS,
                    TelemetrySemantic.STALL_HOLD_CURRENTS,
                )
            ),
        ),
        stall_protection=stall,
        transport=TransportCapability(
            kind=TransportKind.CAN_FD,
            nominal_bitrate=1_000_000,
            # BRS is disabled, so the data phase always runs at the 1 Mbit
            # nominal rate and the interface's configured data bitrate is
            # irrelevant. Leaving it unconstrained lets any dbitrate (1M/5M/7M…)
            # connect and be discovered.
            data_bitrate=None,
            addressing_note=(
                "HOP standard-frame CAN ID pair is fixed per hand side: right "
                "hand request 0x001 / response 0x401, left hand request 0x002 / "
                "response 0x402. CAN FD BRS disabled, so the data phase equals "
                "the 1 Mbit nominal rate and any interface data bitrate works."
            ),
        ),
        operations=OperationCapabilities(
            read_stall_protection=True,
            write_stall_protection=True,
        ),
    )


__all__ = ["declared_capabilities"]
