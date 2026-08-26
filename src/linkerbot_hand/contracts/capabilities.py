"""Device identity and capability contracts."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ..errors import ValidationError
from ._validation import is_finite_number
from .enums import (
    CommandKind,
    HandSide,
    JointEndpoint,
    JointMotionType,
    PercentageSemantics,
    SensorType,
    TransportType,
)


@dataclass(frozen=True, slots=True)
class NumericRange:
    """Validated numeric range and unit metadata.

    Attributes:
        minimum: Inclusive lower bound.
        maximum: Inclusive upper bound.
        unit: Explicit value unit, such as ``percent`` or ``protocol_raw_u8``.
        integer_only: Whether values must be Python integers.
        step: Optional increment measured from ``minimum``.

    Raises:
        ValidationError: If endpoints or step are invalid, bounds are reversed,
            or the unit is empty.
    """

    minimum: float
    maximum: float
    unit: str
    integer_only: bool = False
    step: float | None = None

    def __post_init__(self) -> None:
        """Validate range bounds, unit, and optional step."""
        for name, value in (("minimum", self.minimum), ("maximum", self.maximum)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError(f"range {name} must be numeric")
        if not is_finite_number(self.minimum) or not is_finite_number(self.maximum):
            raise ValidationError("range endpoints must be finite")
        if self.minimum > self.maximum:
            raise ValidationError("range minimum must not exceed maximum")
        if not isinstance(self.unit, str) or not self.unit:
            raise ValidationError("range unit must be a non-empty str")
        if not isinstance(self.integer_only, bool):
            raise ValidationError("range integer_only must be bool")
        if self.step is not None:
            if isinstance(self.step, bool) or not isinstance(self.step, (int, float)):
                raise ValidationError("range step must be numeric")
            if not is_finite_number(self.step) or self.step <= 0:
                raise ValidationError("range step must be finite and positive")


@dataclass(frozen=True, slots=True)
class PercentageMapping:
    """Mapping between a public 0..100 position and protocol values.

    ``raw_closed`` and ``raw_open`` remain for API compatibility. For
    directional joints they mean the raw values at 0 and 100 respectively;
    use :attr:`raw_at_zero` and :attr:`raw_at_hundred` in new code.

    Attributes:
        raw_open: Protocol value mapped to 100; retained under its historical
            name for compatibility.
        raw_closed: Protocol value mapped to 0; retained under its historical
            name for compatibility.
        physical_angle: Calibrated physical range, or ``None`` when unavailable.
        semantics: Whether the percentage means openness or generic position.
        zero_endpoint: Physical pose represented by zero.
        hundred_endpoint: Physical pose represented by one hundred.

    Raises:
        ValidationError: If raw endpoints are equal or invalid, physical angle
            metadata has the wrong type, or endpoint semantics are inconsistent.
    """

    raw_open: int
    raw_closed: int
    physical_angle: NumericRange | None = None
    semantics: PercentageSemantics = PercentageSemantics.OPENNESS
    zero_endpoint: JointEndpoint = JointEndpoint.FLEXED
    hundred_endpoint: JointEndpoint = JointEndpoint.EXTENDED

    def __post_init__(self) -> None:
        """Validate endpoints and normalized-position semantics."""
        if type(self.raw_open) is not int or type(self.raw_closed) is not int:
            raise ValidationError("percentage raw endpoints must be int")
        if self.raw_open == self.raw_closed:
            raise ValidationError("percentage raw endpoints must be distinct")
        if self.physical_angle is not None and not isinstance(
            self.physical_angle, NumericRange
        ):
            raise ValidationError("physical_angle must be NumericRange or None")
        if not isinstance(self.semantics, PercentageSemantics):
            raise ValidationError("percentage semantics must be PercentageSemantics")
        if not isinstance(self.zero_endpoint, JointEndpoint):
            raise ValidationError("zero_endpoint must be JointEndpoint")
        if not isinstance(self.hundred_endpoint, JointEndpoint):
            raise ValidationError("hundred_endpoint must be JointEndpoint")
        if self.zero_endpoint is self.hundred_endpoint:
            raise ValidationError("percentage endpoints must describe distinct poses")
        if self.semantics is PercentageSemantics.OPENNESS and (
            self.zero_endpoint is not JointEndpoint.FLEXED
            or self.hundred_endpoint is not JointEndpoint.EXTENDED
        ):
            raise ValidationError(
                "openness mapping must use flexed and extended endpoints"
            )

    @property
    def raw_at_zero(self) -> int:
        """Return the protocol value represented by normalized zero."""
        return self.raw_closed

    @property
    def raw_at_hundred(self) -> int:
        """Return the protocol value represented by normalized one hundred."""
        return self.raw_open

    @property
    def physical_angle_supported(self) -> bool:
        """Whether a calibrated physical-angle conversion is available."""
        return self.physical_angle is not None

    @property
    def percentage_readback_supported(self) -> bool:
        """Whether protocol values can be converted back to a percentage."""
        return True


@dataclass(frozen=True, slots=True)
class JointCapability:
    """Control ranges and mapping metadata for one ordered joint.

    Attributes:
        index: Zero-based protocol and public sequence index.
        name: Stable public joint name.
        raw_angle_range: Valid protocol angle range, or ``None`` if unsupported.
        percentage_mapping: Verified normalized mapping, or ``None`` if unknown.
        speed_range: Accepted speed range and unit, or ``None`` if unsupported.
        torque_range: Accepted torque range and unit, or ``None`` if unsupported.
        percentage_speed_supported: Whether ``0..100`` speed conversion is an
            explicitly documented linear mapping over ``speed_range``.
        motion_type: Mechanical motion category for interpreting the axis.
        coupled_with: Joint names mechanically coupled to this joint.

    Raises:
        ValidationError: If metadata types, index, coupling, or mapping ranges
            are inconsistent.
    """

    index: int
    name: str
    raw_angle_range: NumericRange | None
    percentage_mapping: PercentageMapping | None
    speed_range: NumericRange | None = None
    torque_range: NumericRange | None = None
    percentage_speed_supported: bool = False
    motion_type: JointMotionType = JointMotionType.UNKNOWN
    coupled_with: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate joint metadata and mapping consistency."""
        if type(self.index) is not int or self.index < 0:
            raise ValidationError("joint index must be a non-negative int")
        if not isinstance(self.name, str) or not self.name:
            raise ValidationError("joint name must be a non-empty str")
        if self.raw_angle_range is not None and not isinstance(
            self.raw_angle_range, NumericRange
        ):
            raise ValidationError("raw_angle_range must be NumericRange or None")
        if self.percentage_mapping is not None and not isinstance(
            self.percentage_mapping, PercentageMapping
        ):
            raise ValidationError(
                "percentage_mapping must be PercentageMapping or None"
            )
        if self.percentage_mapping is not None and self.raw_angle_range is None:
            raise ValidationError(
                "percentage_mapping requires a verified raw_angle_range"
            )
        for name, value in (
            ("speed_range", self.speed_range),
            ("torque_range", self.torque_range),
        ):
            if value is not None and not isinstance(value, NumericRange):
                raise ValidationError(f"{name} must be NumericRange or None")
        if not isinstance(self.percentage_speed_supported, bool):
            raise ValidationError("percentage_speed_supported must be bool")
        if self.percentage_speed_supported and self.speed_range is None:
            raise ValidationError("percentage_speed_supported requires a speed_range")
        if not isinstance(self.motion_type, JointMotionType):
            raise ValidationError("motion_type must be JointMotionType")
        if not isinstance(self.coupled_with, tuple) or any(
            not isinstance(name, str) or not name for name in self.coupled_with
        ):
            raise ValidationError(
                "coupled_with must be a tuple of non-empty str values"
            )
        if self.name in self.coupled_with:
            raise ValidationError("joint cannot be coupled with itself")
        if len(set(self.coupled_with)) != len(self.coupled_with):
            raise ValidationError("coupled_with values must be unique")
        if self.percentage_mapping is not None and self.raw_angle_range is not None:
            for name, endpoint in (
                ("raw_open", self.percentage_mapping.raw_open),
                ("raw_closed", self.percentage_mapping.raw_closed),
            ):
                if (
                    not self.raw_angle_range.minimum
                    <= endpoint
                    <= self.raw_angle_range.maximum
                ):
                    raise ValidationError(
                        f"percentage {name} must be within raw_angle_range"
                    )

    @property
    def raw_angle_control_supported(self) -> bool:
        """Whether the joint accepts exact protocol angle values."""
        return self.raw_angle_range is not None

    @property
    def percentage_angle_control_supported(self) -> bool:
        """Whether the joint has a verified normalized angle mapping."""
        return self.percentage_mapping is not None


@dataclass(frozen=True, slots=True)
class SensorCapability:
    """Polling recommendation and staleness threshold for one sensor.

    Attributes:
        sensor: Supported sensor channel.
        recommended_hz: Recommended polling frequency in hertz.
        stale_after_s: Age in seconds after which cached data is stale.
        unit: Protocol unit used by decoded values, when known.
        encoding: Scalar wire encoding, such as ``u8`` or ``i16_le``.
        value_range: Shared decoded scalar range, when one range applies to all
            values. Angle ranges remain joint-specific.

    Raises:
        ValidationError: If the sensor type or either positive finite value is
            invalid.
    """

    sensor: SensorType
    recommended_hz: float
    stale_after_s: float
    unit: str | None = None
    encoding: str | None = None
    value_range: NumericRange | None = None

    def __post_init__(self) -> None:
        """Validate sensor metadata and timing values."""
        if not isinstance(self.sensor, SensorType):
            raise ValidationError("sensor must be SensorType")
        if self.unit is not None and (not isinstance(self.unit, str) or not self.unit):
            raise ValidationError("sensor unit must be a non-empty str or None")
        if self.encoding is not None and (
            not isinstance(self.encoding, str) or not self.encoding
        ):
            raise ValidationError("sensor encoding must be a non-empty str or None")
        if self.value_range is not None and not isinstance(
            self.value_range, NumericRange
        ):
            raise ValidationError("sensor value_range must be NumericRange or None")
        if (
            self.unit is not None
            and self.value_range is not None
            and self.value_range.unit != self.unit
        ):
            raise ValidationError("sensor value_range unit must match sensor unit")
        for name, value in (
            ("recommended_hz", self.recommended_hz),
            ("stale_after_s", self.stale_after_s),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError(f"{name} must be numeric")
        if not is_finite_number(self.recommended_hz) or self.recommended_hz <= 0:
            raise ValidationError("recommended_hz must be finite and positive")
        if not is_finite_number(self.stale_after_s) or self.stale_after_s <= 0:
            raise ValidationError("stale_after_s must be finite and positive")


@dataclass(frozen=True, slots=True)
class DeviceFeatures:
    """Optional discrete operations and ACK behavior supported by a device.

    Attributes:
        device_ack: Whether any command receives an explicit protocol ACK.
        clear_faults: Whether fault clearing is supported.
        enable: Whether enable and disable commands are supported.
        calibration: Whether device calibration is supported.
        acknowledged_commands: Exact command kinds with protocol ACK support.

    Raises:
        ValidationError: If flags or ACK command declarations are inconsistent.
    """

    device_ack: bool = False
    clear_faults: bool = False
    enable: bool = False
    calibration: bool = False
    acknowledged_commands: tuple[CommandKind, ...] = ()

    def __post_init__(self) -> None:
        """Validate feature flags and explicit ACK declarations."""
        for name in ("device_ack", "clear_faults", "enable", "calibration"):
            if not isinstance(getattr(self, name), bool):
                raise ValidationError(f"{name} must be bool")
        if not isinstance(self.acknowledged_commands, tuple) or any(
            not isinstance(command, CommandKind)
            for command in self.acknowledged_commands
        ):
            raise ValidationError(
                "acknowledged_commands must be a tuple of CommandKind values"
            )
        if len(set(self.acknowledged_commands)) != len(self.acknowledged_commands):
            raise ValidationError("acknowledged_commands must be unique")
        if not self.device_ack and self.acknowledged_commands:
            raise ValidationError("acknowledged_commands requires device_ack support")
        if self.device_ack and not self.acknowledged_commands:
            raise ValidationError("device_ack requires explicit acknowledged_commands")

    def supports_ack(self, command: CommandKind) -> bool:
        """Return whether a command receives an explicit protocol ACK.

        Args:
            command: Command kind to inspect.

        Returns:
            ``True`` only when the protocol explicitly acknowledges the kind.
        """
        return self.device_ack and command in self.acknowledged_commands


@dataclass(frozen=True, slots=True)
class DeviceCapabilities:
    """SDK source of truth for one model, side, and firmware identity.

    Attributes:
        model: Canonical model name.
        firmware: Detected firmware version, or ``None`` before detection.
        side: Configured or detected hand side.
        joints: Ordered joint metadata matching command and telemetry values.
        sensors: Supported sensor channels and polling recommendations.
        transport: Required transport family.
        features: Optional device operations and ACK behavior.
        percentage_convention: Compatibility declaration for the public
            normalized API. Flexion axes use 0=flexed and 100=extended;
            directional axes describe their endpoints through mapping metadata.

    Raises:
        ValidationError: If capability metadata is malformed or internally
            inconsistent.
    """

    model: str
    firmware: str | None
    side: HandSide
    joints: tuple[JointCapability, ...]
    sensors: tuple[SensorCapability, ...]
    transport: TransportType
    features: DeviceFeatures = field(default_factory=DeviceFeatures)
    percentage_convention: str = "100=open, 0=closed"
    _side_joint_variants: tuple[tuple[HandSide, tuple[JointCapability, ...]], ...] = (
        field(default=(), repr=False, compare=False)
    )

    def __post_init__(self) -> None:
        """Validate ordering, uniqueness, coupling, and side variants."""
        if not isinstance(self.model, str) or not self.model:
            raise ValidationError("model must be a non-empty str")
        if self.firmware is not None and not isinstance(self.firmware, str):
            raise ValidationError("firmware must be str or None")
        if not isinstance(self.side, HandSide):
            raise ValidationError("side must be HandSide")
        if not isinstance(self.joints, tuple) or any(
            not isinstance(joint, JointCapability) for joint in self.joints
        ):
            raise ValidationError("joints must be a tuple of JointCapability values")
        if not isinstance(self.sensors, tuple) or any(
            not isinstance(sensor, SensorCapability) for sensor in self.sensors
        ):
            raise ValidationError("sensors must be a tuple of SensorCapability values")
        if not isinstance(self.transport, TransportType):
            raise ValidationError("transport must be TransportType")
        if not isinstance(self.features, DeviceFeatures):
            raise ValidationError("features must be DeviceFeatures")
        if self.percentage_convention != "100=open, 0=closed":
            raise ValidationError('percentage_convention must be "100=open, 0=closed"')
        if tuple(joint.index for joint in self.joints) != tuple(
            range(len(self.joints))
        ):
            raise ValidationError("joint indices must be contiguous and ordered")
        if len({joint.name for joint in self.joints}) != len(self.joints):
            raise ValidationError("joint names must be unique")
        joint_names = {joint.name for joint in self.joints}
        if any(
            coupled_name not in joint_names
            for joint in self.joints
            for coupled_name in joint.coupled_with
        ):
            raise ValidationError("coupled_with must reference device joint names")
        if len({sensor.sensor for sensor in self.sensors}) != len(self.sensors):
            raise ValidationError("sensor capabilities must be unique")
        if not isinstance(self._side_joint_variants, tuple):
            raise ValidationError("side joint variants must be a tuple")
        variant_sides: list[HandSide] = []
        for variant in self._side_joint_variants:
            if (
                not isinstance(variant, tuple)
                or len(variant) != 2
                or not isinstance(variant[0], HandSide)
                or not isinstance(variant[1], tuple)
                or any(not isinstance(joint, JointCapability) for joint in variant[1])
            ):
                raise ValidationError("side joint variants are invalid")
            variant_sides.append(variant[0])
            if tuple(joint.name for joint in variant[1]) != self.joint_names:
                raise ValidationError(
                    "side joint variants must preserve joint names and order"
                )
        if len(set(variant_sides)) != len(variant_sides):
            raise ValidationError("side joint variants must use unique sides")

    @property
    def joint_names(self) -> tuple[str, ...]:
        """Return joint names in command and telemetry order."""
        return tuple(joint.name for joint in self.joints)

    @property
    def joint_count(self) -> int:
        """Return the number of ordered joints."""
        return len(self.joints)

    def sensor(self, sensor: SensorType) -> SensorCapability | None:
        """Look up capability metadata for a sensor channel.

        Args:
            sensor: Sensor channel to find.

        Returns:
            Matching metadata, or ``None`` when the sensor is unsupported.
        """
        return next((item for item in self.sensors if item.sensor is sensor), None)

    def supports_sensor(self, sensor: SensorType) -> bool:
        """Return whether a sensor channel is supported.

        Args:
            sensor: Sensor channel to test.
        """
        return self.sensor(sensor) is not None

    def with_side(self, side: HandSide) -> DeviceCapabilities:
        """Return capabilities specialized for a hand side.

        Args:
            side: Side to apply. Models without side-specific mappings retain
                their common joint declarations.

        Returns:
            An immutable capability copy with the selected side and joints.

        Raises:
            ValidationError: If ``side`` is not a :class:`HandSide`.
        """
        if not isinstance(side, HandSide):
            raise ValidationError("side must be HandSide")
        joints = next(
            (
                variant_joints
                for variant_side, variant_joints in self._side_joint_variants
                if variant_side is side
            ),
            self.joints,
        )
        return replace(self, side=side, joints=joints)

    def with_identity(self, identity: DeviceIdentity) -> DeviceCapabilities:
        """Merge verified device identity into static model capabilities.

        Args:
            identity: Identity returned by the opened backend.

        Returns:
            Capabilities updated with detected side and firmware when present.

        Raises:
            ValidationError: If ``identity`` has the wrong type.
        """
        if not isinstance(identity, DeviceIdentity):
            raise ValidationError("identity must be DeviceIdentity")
        side = identity.side if identity.side is not HandSide.UNKNOWN else self.side
        return replace(
            self.with_side(side),
            firmware=identity.firmware or self.firmware,
        )


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """Identity read from a connected device.

    Attributes:
        model: Canonical or protocol-reported model name.
        firmware: Protocol-reported firmware version, if available.
        side: Protocol-reported hand side, if available.
        serial_number: Protocol-reported serial number, if available.

    Raises:
        ValidationError: If identity fields have invalid types or model is empty.
    """

    model: str
    firmware: str | None = None
    side: HandSide = HandSide.UNKNOWN
    serial_number: str | None = None

    def __post_init__(self) -> None:
        """Validate device identity fields."""
        if not isinstance(self.model, str) or not self.model:
            raise ValidationError("identity model must be a non-empty str")
        if self.firmware is not None and not isinstance(self.firmware, str):
            raise ValidationError("identity firmware must be str or None")
        if not isinstance(self.side, HandSide):
            raise ValidationError("identity side must be HandSide")
        if self.serial_number is not None and not isinstance(self.serial_number, str):
            raise ValidationError("serial_number must be str or None")


__all__ = [
    "DeviceCapabilities",
    "DeviceFeatures",
    "DeviceIdentity",
    "JointCapability",
    "NumericRange",
    "PercentageMapping",
    "SensorCapability",
]
