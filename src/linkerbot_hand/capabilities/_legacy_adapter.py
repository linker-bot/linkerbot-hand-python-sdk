"""Temporary adapter from 1.0 profiles to the pre-1.0 runtime contracts."""

from __future__ import annotations

from ..api.commands import AckSupport
from ..api.enums import HandSide, JointMotionType, TransportKind
from ..contracts import (
    CommandKind,
    SensorType,
    TransportType,
)
from ..contracts import (
    DeviceCapabilities as LegacyDeviceCapabilities,
)
from ..contracts import (
    DeviceFeatures as LegacyDeviceFeatures,
)
from ..contracts import (
    HandSide as LegacyHandSide,
)
from ..contracts import (
    JointCapability as LegacyJointCapability,
)
from ..contracts import (
    JointEndpoint as LegacyJointEndpoint,
)
from ..contracts import (
    JointMotionType as LegacyJointMotionType,
)
from ..contracts import (
    PercentageMapping as LegacyPercentageMapping,
)
from ..contracts import (
    PercentageSemantics as LegacyPercentageSemantics,
)
from ..contracts import (
    SensorCapability as LegacySensorCapability,
)
from ._legacy_metadata import joint_range, sensor_metadata, sensor_range
from .types import DeviceCapabilities
from .values import CommandSemantic, TelemetrySemantic

_LEGACY_NAMES = {
    "L6": ("thumb_flex", "thumb_abd", "index", "middle", "ring", "pinky"),
    "O6": ("thumb_flex", "thumb_abd", "index", "middle", "ring", "pinky"),
    "L20Lite": (
        "thumb_flex",
        "thumb_abd",
        "index_flex",
        "middle_flex",
        "ring_flex",
        "pinky_flex",
        "index_abd",
        "ring_abd",
        "pinky_abd",
        "thumb_yaw",
    ),
    "L25": (
        "thumb_abd",
        "thumb_yaw",
        "thumb_root1",
        "thumb_tip",
        "index_abd",
        "index_root1",
        "index_tip",
        "middle_abd",
        "middle_root1",
        "middle_tip",
        "ring_abd",
        "ring_root1",
        "ring_tip",
        "pinky_abd",
        "pinky_root1",
        "pinky_tip",
    ),
    "O20": (
        "thumb_mcp",
        "thumb_ip",
        "thumb_abd",
        "thumb_cmc",
        "index_abd",
        "index_mcp",
        "index_pip",
        "middle_abd",
        "middle_mcp",
        "middle_pip",
        "ring_abd",
        "ring_mcp",
        "ring_pip",
        "pinky_abd",
        "pinky_mcp",
        "pinky_dip",
    ),
    "L30": tuple(f"j{index}" for index in range(1, 18)),
    "O30i": (
        "roll_0",
        "yaw_0",
        "yaw_1",
        "yaw_2",
        "yaw_3",
        "yaw_4",
        "root1_0",
        "root1_1",
        "root1_2",
        "root1_3",
        "root1_4",
        "root2_1",
        "root2_2",
        "root2_3",
        "root2_4",
        "tip_0",
        "tip_1",
        "tip_2",
        "tip_3",
        "tip_4",
    ),
}


def to_legacy_capabilities(
    capabilities: DeviceCapabilities,
) -> LegacyDeviceCapabilities:
    """Build the old runtime view without maintaining a second model table."""

    legacy_names = _LEGACY_NAMES[capabilities.model]
    legacy_name_by_new = dict(zip(capabilities.joint_names, legacy_names, strict=True))
    speed_command = capabilities.command(CommandSemantic.SPEEDS)
    limit_command = next(
        (
            capabilities.command(semantic)
            for semantic in (
                CommandSemantic.CURRENT_LIMITS,
                CommandSemantic.EFFORT_LIMITS,
                CommandSemantic.TORQUES,
            )
            if capabilities.command(semantic) is not None
        ),
        None,
    )
    joints = tuple(
        LegacyJointCapability(
            index=joint.index,
            name=legacy_names[joint.index],
            raw_angle_range=joint_range(
                capabilities.model,
                joint.index,
                joint.raw_angle_range,
                "angle",
            ),
            percentage_mapping=(
                None
                if joint.percent_mapping is None
                else LegacyPercentageMapping(
                    raw_open=int(joint.percent_mapping.raw_at_hundred),
                    raw_closed=int(joint.percent_mapping.raw_at_zero),
                    semantics=(
                        LegacyPercentageSemantics.OPENNESS
                        if joint.motion_type is JointMotionType.FLEXION
                        else LegacyPercentageSemantics.POSITION
                    ),
                    zero_endpoint=LegacyJointEndpoint(joint.percent_mapping.zero_label),
                    hundred_endpoint=LegacyJointEndpoint(
                        joint.percent_mapping.hundred_label
                    ),
                )
            ),
            speed_range=(
                None
                if speed_command is None
                else joint_range(
                    capabilities.model,
                    joint.index,
                    speed_command.range_for(joint.index),
                    "speed",
                )
            ),
            torque_range=(
                None
                if limit_command is None
                else joint_range(
                    capabilities.model,
                    joint.index,
                    limit_command.range_for(joint.index),
                    "limit",
                )
            ),
            percentage_speed_supported=(
                speed_command is not None
                and capabilities.model in {"L6", "O6", "L20Lite", "L25"}
            ),
            motion_type=LegacyJointMotionType(joint.motion_type.value),
            coupled_with=tuple(
                legacy_name_by_new[value] for value in joint.coupled_with
            ),
        )
        for joint in capabilities.joints
    )
    sensors = _legacy_sensors(capabilities)
    acknowledged_commands = tuple(
        command
        for command in (
            _legacy_command(value.semantic)
            for value in capabilities.commands
            if value.ack_support is AckSupport.SUPPORTED
        )
        if command is not None
    ) + tuple(
        {
            "enable": CommandKind.ENABLE,
            "disable": CommandKind.DISABLE,
            "calibrate": CommandKind.CALIBRATE,
        }[operation]
        for operation in capabilities.operations.acknowledged_operations
    )
    side = {
        HandSide.LEFT: LegacyHandSide.LEFT,
        HandSide.RIGHT: LegacyHandSide.RIGHT,
        HandSide.NOT_APPLICABLE: LegacyHandSide.UNKNOWN,
    }[capabilities.side]
    return LegacyDeviceCapabilities(
        model=capabilities.model,
        firmware=capabilities.identity.firmware,
        side=side,
        joints=joints,
        sensors=sensors,
        transport=(
            TransportType.CAN
            if capabilities.transport.kind is TransportKind.CAN
            else TransportType.CAN_FD
        ),
        features=LegacyDeviceFeatures(
            device_ack=bool(acknowledged_commands),
            clear_faults=capabilities.operations.clear_faults,
            enable=(capabilities.operations.enable and capabilities.operations.disable),
            calibration=capabilities.operations.calibrate,
            acknowledged_commands=acknowledged_commands,
        ),
    )


def _legacy_command(semantic: CommandSemantic) -> CommandKind | None:
    return {
        CommandSemantic.ANGLES: CommandKind.ANGLES,
        CommandSemantic.SPEEDS: CommandKind.SPEEDS,
        CommandSemantic.CURRENT_LIMITS: CommandKind.TORQUES,
        CommandSemantic.EFFORT_LIMITS: CommandKind.TORQUES,
        CommandSemantic.TORQUES: CommandKind.TORQUES,
    }.get(semantic)


def _legacy_sensors(
    capabilities: DeviceCapabilities,
) -> tuple[LegacySensorCapability, ...]:
    result: list[LegacySensorCapability] = []
    used: set[SensorType] = set()
    for value in capabilities.telemetry:
        sensor = {
            TelemetrySemantic.ANGLES: SensorType.ANGLES,
            TelemetrySemantic.MEASURED_SPEEDS: SensorType.SPEEDS,
            TelemetrySemantic.CONFIGURED_SPEEDS: SensorType.SPEEDS,
            TelemetrySemantic.CONFIGURED_ACCELERATIONS: SensorType.ACCELERATION,
            TelemetrySemantic.CURRENTS: SensorType.CURRENT,
            TelemetrySemantic.CURRENT_LIMITS: SensorType.TORQUES,
            TelemetrySemantic.EFFORT_LIMITS: SensorType.TORQUES,
            TelemetrySemantic.TORQUES: SensorType.TORQUES,
            TelemetrySemantic.TEMPERATURES: SensorType.TEMPERATURE,
            TelemetrySemantic.FORCE: SensorType.FORCE,
            TelemetrySemantic.FAULTS: SensorType.FAULTS,
            TelemetrySemantic.VOLTAGES: SensorType.VOLTAGE,
            TelemetrySemantic.MOTION_TIMES: SensorType.MOTION_TIME,
        }.get(value.semantic)
        if (
            sensor is None
            or sensor in used
            or not value.device_queried
            or (capabilities.model == "L30" and sensor is SensorType.TORQUES)
        ):
            continue
        used.add(sensor)
        unit, encoding, physical_unit, scale, offset, value_kind = sensor_metadata(
            capabilities.model,
            sensor,
            value.raw_unit or "dimensionless",
            value.raw_encoding,
        )
        source_range = value.raw_range
        if (
            source_range is None
            and sensor is SensorType.ANGLES
            and capabilities.model not in {"L30", "O20"}
        ):
            source_range = capabilities.joints[0].raw_angle_range
        result.append(
            LegacySensorCapability(
                sensor=sensor,
                recommended_hz=(
                    30
                    if capabilities.model == "L6" and sensor is SensorType.FORCE
                    else value.recommended_hz
                ),
                stale_after_s=value.stale_after_s,
                unit=unit,
                encoding=encoding,
                value_range=(
                    None
                    if source_range is None
                    else sensor_range(
                        source_range,
                        unit,
                        physical_unit,
                        scale,
                        offset,
                        model=capabilities.model,
                        sensor=sensor,
                    )
                ),
                physical_unit=physical_unit,
                scale=scale,
                offset=offset,
                value_kind=value_kind,
                matrix_shape=(
                    (value.shape[-2], value.shape[-1])
                    if value.semantic is TelemetrySemantic.FORCE
                    else None
                ),
            )
        )
    return tuple(result)


__all__ = ["to_legacy_capabilities"]
