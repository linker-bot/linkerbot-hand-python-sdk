"""Small constructors shared by immutable model capability declarations."""

from __future__ import annotations

from ...api.commands import AckSupport
from ...api.enums import (
    Confidence,
    HandSide,
    JointMotionType,
    ReadMode,
    TransportKind,
)
from ..ranges import NumericRange, PercentMapping
from ..types import (
    CommandCapability,
    CommandSemantic,
    IdentityCapability,
    JointCapability,
    TelemetryCapability,
    TelemetrySemantic,
    TransportCapability,
)

U8_RANGE = NumericRange(0, 255, integer_only=True, step=1)


def identity(
    model: str,
    side: HandSide,
    *,
    supported_fields: tuple[str, ...],
) -> IdentityCapability:
    """Create an unverified identity available before device I/O."""

    return IdentityCapability(
        model=model,
        side=side,
        verified=False,
        supported_fields=supported_fields,
    )


def joint(
    index: int,
    name: str,
    motion_type: JointMotionType,
    raw_range: NumericRange,
    *,
    raw_at_zero: float,
    raw_at_hundred: float,
    zero_label: str,
    hundred_label: str,
    confidence: Confidence = Confidence.MEASURED,
    coupled_with: tuple[str, ...] = (),
) -> JointCapability:
    """Create one measured linear percent mapping over a raw range."""

    return JointCapability(
        index=index,
        name=name,
        motion_type=motion_type,
        raw_angle_range=raw_range,
        percent_mapping=PercentMapping(
            raw_at_zero=raw_at_zero,
            raw_at_hundred=raw_at_hundred,
            zero_label=zero_label,
            hundred_label=hundred_label,
            confidence=confidence,
        ),
        coupled_with=coupled_with,
    )


def angle_command(
    joints: tuple[JointCapability, ...],
    raw_encoding: str,
    *,
    ack_support: AckSupport = AckSupport.UNSUPPORTED,
    returns_current_state: bool = False,
) -> CommandCapability:
    """Build the required exact-raw angle command capability."""

    return CommandCapability(
        semantic=CommandSemantic.ANGLES,
        channels=tuple(value.name for value in joints),
        raw_ranges=tuple(value.raw_angle_range for value in joints),
        raw_encoding=raw_encoding,
        raw_unit="count",
        raw_scale_note=None,
        unit_confidence=Confidence.MEASURED,
        ack_support=ack_support,
        returns_current_state=returns_current_state,
        percent_mappings=tuple(value.percent_mapping for value in joints),
    )


def command(
    semantic: CommandSemantic,
    channels: tuple[str, ...],
    raw_range: NumericRange | tuple[NumericRange, ...],
    raw_encoding: str,
    *,
    raw_unit: str | None,
    raw_scale_note: str | None = None,
    unit_confidence: Confidence = Confidence.UNKNOWN,
    ack_support: AckSupport = AckSupport.UNSUPPORTED,
) -> CommandCapability:
    """Build a raw-only writable capability."""

    ranges = raw_range if isinstance(raw_range, tuple) else (raw_range,)
    return CommandCapability(
        semantic=semantic,
        channels=channels,
        raw_ranges=ranges,
        raw_encoding=raw_encoding,
        raw_unit=raw_unit,
        raw_scale_note=raw_scale_note,
        unit_confidence=unit_confidence,
        ack_support=ack_support,
    )


def angle_telemetry(
    joints: tuple[JointCapability, ...],
    raw_encoding: str,
    *,
    recommended_hz: float,
    estimated_transaction_s: float,
    verified_max_hz: float | None = None,
) -> TelemetryCapability:
    """Build the required angle response capability."""

    return telemetry(
        TelemetrySemantic.ANGLES,
        tuple(value.name for value in joints),
        raw_encoding,
        shape=(len(joints),),
        raw_range=None,
        raw_unit="count",
        unit_confidence=Confidence.MEASURED,
        recommended_hz=recommended_hz,
        stale_after_s=max(2 / recommended_hz, 0.02),
        estimated_transaction_s=estimated_transaction_s,
        verified_max_hz=verified_max_hz,
    )


def telemetry(
    semantic: TelemetrySemantic,
    channels: tuple[str, ...],
    raw_encoding: str,
    *,
    shape: tuple[int, ...],
    raw_range: NumericRange | None,
    raw_unit: str | None,
    raw_scale_note: str | None = None,
    unit_confidence: Confidence = Confidence.UNKNOWN,
    recommended_hz: float,
    stale_after_s: float,
    estimated_transaction_s: float,
    verified_max_hz: float | None = None,
    read_mode: ReadMode = ReadMode.QUERY,
    device_measured: bool = True,
    device_queried: bool = True,
    decoded_statuses: bool = False,
) -> TelemetryCapability:
    """Build one typed raw telemetry declaration."""

    return TelemetryCapability(
        semantic=semantic,
        channels=channels,
        read_mode=read_mode,
        raw_encoding=raw_encoding,
        shape=shape,
        raw_range=raw_range,
        raw_unit=raw_unit,
        raw_scale_note=raw_scale_note,
        unit_confidence=unit_confidence,
        recommended_hz=recommended_hz,
        stale_after_s=stale_after_s,
        estimated_transaction_s=estimated_transaction_s,
        verified_max_hz=verified_max_hz,
        device_measured=device_measured,
        device_queried=device_queried,
        decoded_statuses=decoded_statuses,
    )


def transport(kind: TransportKind) -> TransportCapability:
    """Return documented bitrate and addressing requirements."""

    if kind is TransportKind.CAN:
        return TransportCapability(
            kind=kind,
            nominal_bitrate=1_000_000,
            data_bitrate=None,
            addressing_note="Classic CAN endpoint IDs are selected by the protocol profile.",
        )
    return TransportCapability(
        kind=kind,
        nominal_bitrate=1_000_000,
        data_bitrate=5_000_000,
        addressing_note="CAN FD endpoint IDs and BRS are selected by the protocol profile.",
    )


__all__ = [
    "U8_RANGE",
    "angle_command",
    "angle_telemetry",
    "command",
    "identity",
    "joint",
    "telemetry",
    "transport",
]
