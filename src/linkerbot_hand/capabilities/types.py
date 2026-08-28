"""Immutable source-of-truth capability types for Hand SDK 1.0."""

from __future__ import annotations

from dataclasses import dataclass

from ..api.enums import HandSide, JointMotionType, TransportKind
from ..api.errors import ValidationError
from .ranges import NumericRange, PercentMapping, PhysicalAngleMapping
from .values import (
    CommandCapability,
    CommandSemantic,
    StallFieldCapability,
    StallProtectionCapability,
    TelemetryCapability,
    TelemetrySemantic,
)


@dataclass(frozen=True, slots=True)
class IdentityCapability:
    """Declared or verified identity fields for the selected profile."""

    model: str
    side: HandSide
    verified: bool
    firmware: str | None = None
    hardware_revision: str | None = None
    mechanical_revision: str | None = None
    sensor_variant: str | None = None
    serial_number: str | None = None
    product_code: str | None = None
    supported_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model:
            raise ValidationError("model must be a non-empty str")
        if not isinstance(self.side, HandSide):
            raise ValidationError("side must be HandSide")
        if not isinstance(self.verified, bool):
            raise ValidationError("verified must be bool")
        for name in (
            "firmware",
            "hardware_revision",
            "mechanical_revision",
            "sensor_variant",
            "serial_number",
            "product_code",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValidationError(f"{name} must be a non-empty str or None")
        if not isinstance(self.supported_fields, tuple) or any(
            not isinstance(value, str) or not value for value in self.supported_fields
        ):
            raise ValidationError("supported_fields must contain non-empty strings")
        if len(set(self.supported_fields)) != len(self.supported_fields):
            raise ValidationError("supported_fields must be unique")


@dataclass(frozen=True, slots=True)
class JointCapability:
    """Ordered protocol and physical metadata for one installed joint."""

    index: int
    name: str
    motion_type: JointMotionType
    raw_angle_range: NumericRange
    percent_mapping: PercentMapping | None
    physical_angle_mapping: PhysicalAngleMapping | None = None
    coupled_with: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValidationError("joint index must be a non-negative int")
        if not isinstance(self.name, str) or not self.name:
            raise ValidationError("joint name must be a non-empty str")
        if not isinstance(self.motion_type, JointMotionType):
            raise ValidationError("motion_type must be JointMotionType")
        if not isinstance(self.raw_angle_range, NumericRange):
            raise ValidationError("raw_angle_range must be NumericRange")
        if self.percent_mapping is not None and not isinstance(
            self.percent_mapping, PercentMapping
        ):
            raise ValidationError("percent_mapping must be PercentMapping or None")
        if self.physical_angle_mapping is not None and not isinstance(
            self.physical_angle_mapping, PhysicalAngleMapping
        ):
            raise ValidationError(
                "physical_angle_mapping must be PhysicalAngleMapping or None"
            )
        if self.percent_mapping is not None:
            for endpoint in (
                self.percent_mapping.raw_at_zero,
                self.percent_mapping.raw_at_hundred,
            ):
                if not self.raw_angle_range.contains(endpoint):
                    raise ValidationError(
                        "percent endpoints must be inside raw_angle_range"
                    )
        if self.physical_angle_mapping is not None:
            for endpoint in (
                self.physical_angle_mapping.raw_at_first,
                self.physical_angle_mapping.raw_at_second,
            ):
                if not self.raw_angle_range.contains(endpoint):
                    raise ValidationError(
                        "physical endpoints must be inside raw_angle_range"
                    )
        if not isinstance(self.coupled_with, tuple) or any(
            not isinstance(value, str) or not value for value in self.coupled_with
        ):
            raise ValidationError("coupled_with must contain non-empty strings")
        if self.name in self.coupled_with:
            raise ValidationError("a joint cannot be coupled with itself")
        if len(set(self.coupled_with)) != len(self.coupled_with):
            raise ValidationError("coupled_with must be unique")


@dataclass(frozen=True, slots=True)
class TransportCapability:
    """Physical transport and addressing requirements for a profile."""

    kind: TransportKind
    nominal_bitrate: int | None
    data_bitrate: int | None
    addressing_note: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TransportKind):
            raise ValidationError("transport kind must be TransportKind")
        for name in ("nominal_bitrate", "data_bitrate"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValidationError(f"{name} must be a positive int or None")
        if not isinstance(self.addressing_note, str) or not self.addressing_note:
            raise ValidationError("addressing_note must be a non-empty str")
        if self.kind is TransportKind.CAN and self.data_bitrate is not None:
            raise ValidationError("classic CAN must not declare a data bitrate")


@dataclass(frozen=True, slots=True)
class OperationCapabilities:
    """Discrete and identity operations supported by a profile."""

    read_device_info: bool = True
    clear_faults: bool = False
    enable: bool = False
    disable: bool = False
    calibrate: bool = False
    read_stall_protection: bool = False
    write_stall_protection: bool = False
    acknowledged_operations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "read_device_info",
            "clear_faults",
            "enable",
            "disable",
            "calibrate",
            "read_stall_protection",
            "write_stall_protection",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValidationError(f"{name} must be bool")
        if not isinstance(self.acknowledged_operations, tuple) or any(
            not isinstance(value, str) or not value
            for value in self.acknowledged_operations
        ):
            raise ValidationError(
                "acknowledged_operations must contain non-empty strings"
            )
        if len(set(self.acknowledged_operations)) != len(self.acknowledged_operations):
            raise ValidationError("acknowledged_operations must be unique")


@dataclass(frozen=True, slots=True)
class DeviceCapabilities:
    """Complete immutable capability profile selected for one hand endpoint."""

    identity: IdentityCapability
    joints: tuple[JointCapability, ...]
    commands: tuple[CommandCapability, ...]
    telemetry: tuple[TelemetryCapability, ...]
    stall_protection: StallProtectionCapability | None
    transport: TransportCapability
    operations: OperationCapabilities
    firmware_unsupported_telemetry: tuple[TelemetrySemantic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, IdentityCapability):
            raise ValidationError("identity must be IdentityCapability")
        if (
            not isinstance(self.joints, tuple)
            or not self.joints
            or any(not isinstance(value, JointCapability) for value in self.joints)
        ):
            raise ValidationError("joints must contain JointCapability values")
        if tuple(joint.index for joint in self.joints) != tuple(
            range(len(self.joints))
        ):
            raise ValidationError("joint indices must be contiguous and ordered")
        joint_names = self.joint_names
        if len(set(joint_names)) != len(joint_names):
            raise ValidationError("joint names must be unique")
        for joint in self.joints:
            if not set(joint.coupled_with) <= set(joint_names):
                raise ValidationError("coupled joint does not exist")
        if not isinstance(self.commands, tuple) or any(
            not isinstance(value, CommandCapability) for value in self.commands
        ):
            raise ValidationError("commands must contain CommandCapability values")
        if len({value.semantic for value in self.commands}) != len(self.commands):
            raise ValidationError("command semantics must be unique")
        if not isinstance(self.telemetry, tuple) or any(
            not isinstance(value, TelemetryCapability) for value in self.telemetry
        ):
            raise ValidationError("telemetry must contain TelemetryCapability values")
        if len({value.semantic for value in self.telemetry}) != len(self.telemetry):
            raise ValidationError("telemetry semantics must be unique")
        if self.stall_protection is not None and not isinstance(
            self.stall_protection, StallProtectionCapability
        ):
            raise ValidationError(
                "stall_protection must be StallProtectionCapability or None"
            )
        if not isinstance(self.transport, TransportCapability):
            raise ValidationError("transport must be TransportCapability")
        if not isinstance(self.operations, OperationCapabilities):
            raise ValidationError("operations must be OperationCapabilities")
        self._validate_firmware_unsupported()
        angle_command = self.command(CommandSemantic.ANGLES)
        angle_telemetry = self.telemetry_channel(TelemetrySemantic.ANGLES)
        if angle_command is None or angle_command.channels != joint_names:
            raise ValidationError("angle command channels must match joints")
        if angle_command.raw_ranges != tuple(
            joint.raw_angle_range for joint in self.joints
        ):
            raise ValidationError("angle command ranges must match joints")
        if angle_command.percent_mappings != tuple(
            joint.percent_mapping for joint in self.joints
        ):
            raise ValidationError("angle command percent mappings must match joints")
        if angle_telemetry is None or angle_telemetry.channels != joint_names:
            raise ValidationError("angle telemetry channels must match joints")
        if angle_command.returns_current_state and not angle_telemetry.device_measured:
            raise ValidationError(
                "current-state angle command feedback requires measured telemetry"
            )
        if angle_telemetry.shape != (len(self.joints),):
            raise ValidationError("angle telemetry shape must match joints")
        if self.stall_protection is not None and (
            self.operations.read_stall_protection
            != self.stall_protection.read_supported
            or self.operations.write_stall_protection
            != self.stall_protection.write_supported
        ):
            raise ValidationError("stall operation flags must match metadata")

    @property
    def model(self) -> str:
        """Return the canonical model name."""

        return self.identity.model

    @property
    def side(self) -> HandSide:
        """Return configured or verified physical side."""

        return self.identity.side

    @property
    def joint_names(self) -> tuple[str, ...]:
        """Return public joint names in protocol order."""

        return tuple(joint.name for joint in self.joints)

    def command(self, semantic: CommandSemantic) -> CommandCapability | None:
        """Look up writable metadata without device I/O."""

        return next(
            (value for value in self.commands if value.semantic is semantic),
            None,
        )

    def telemetry_channel(
        self,
        semantic: TelemetrySemantic,
    ) -> TelemetryCapability | None:
        """Look up readable/cache metadata without device I/O."""

        return next(
            (value for value in self.telemetry if value.semantic is semantic),
            None,
        )

    def telemetry_supported(self, semantic: TelemetrySemantic) -> bool:
        """Return whether this channel is declared and not runtime-disabled."""

        return (
            self.telemetry_channel(semantic) is not None
            and semantic not in self.firmware_unsupported_telemetry
        )

    def _validate_firmware_unsupported(self) -> None:
        telemetry_values = self.firmware_unsupported_telemetry
        if not isinstance(telemetry_values, tuple) or any(
            not isinstance(value, TelemetrySemantic) for value in telemetry_values
        ):
            raise ValidationError(
                "firmware_unsupported_telemetry must contain TelemetrySemantic values"
            )
        if len(set(telemetry_values)) != len(telemetry_values):
            raise ValidationError("firmware_unsupported_telemetry must be unique")
        if any(self.telemetry_channel(value) is None for value in telemetry_values):
            raise ValidationError(
                "firmware-unsupported telemetry must remain declared for metadata"
            )


__all__ = [
    "CommandCapability",
    "CommandSemantic",
    "DeviceCapabilities",
    "IdentityCapability",
    "JointCapability",
    "NumericRange",
    "OperationCapabilities",
    "PercentMapping",
    "PhysicalAngleMapping",
    "StallFieldCapability",
    "StallProtectionCapability",
    "TelemetryCapability",
    "TelemetrySemantic",
    "TransportCapability",
]
