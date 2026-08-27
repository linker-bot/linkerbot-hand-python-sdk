"""O20 protocol backend for the unified device runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from linkerbot_transport import CANRouteKey

from ...api.enums import DataOrigin, HandSide
from ...api.errors import ProtocolError, UnsupportedOperationError
from ...api.readings import RawNumber
from ...capabilities.types import (
    CommandSemantic,
    DeviceCapabilities,
    TelemetrySemantic,
)
from ..base import ProtocolTransaction, ResponseProgress, SamplePayload
from .codec import (
    REG_ANGLES,
    REG_CONFIGURED_SPEEDS,
    REG_CURRENTS,
    REG_DEVICE_INFO,
    REG_EFFORT_LIMITS,
    REG_FAULTS,
    REG_MEASURED_SPEEDS,
    REG_TACTILE_FIRST,
    REG_TACTILE_LAST,
    REG_TARGET_ANGLES,
    REG_TEMPERATURES,
    build_read_message,
    build_write_message,
    decode_i16_vector,
    decode_u8_vector,
    decode_u16_vector,
    device_id_for_side,
    encode_i16_vector,
    encode_u16_vector,
    encode_zero_faults,
    matching_read_data,
    read_route,
)
from .identity import o20_identity_transaction
from .tactile import tactile_transaction

_Decoder = Callable[[bytes], tuple[int, ...]]
_Encoder = Callable[[tuple[RawNumber, ...]], bytes]


@dataclass(frozen=True, slots=True)
class _CommandObject:
    register: int
    encoder: _Encoder
    dlc: int


@dataclass(frozen=True, slots=True)
class _ReadObject:
    register: int
    decoder: _Decoder
    origin: DataOrigin = DataOrigin.DEVICE_RESPONSE


_COMMAND_OBJECTS = {
    CommandSemantic.ANGLES: _CommandObject(REG_TARGET_ANGLES, encode_i16_vector, 14),
    CommandSemantic.SPEEDS: _CommandObject(
        REG_CONFIGURED_SPEEDS,
        encode_i16_vector,
        14,
    ),
    CommandSemantic.EFFORT_LIMITS: _CommandObject(
        REG_EFFORT_LIMITS,
        encode_u16_vector,
        14,
    ),
}
_READ_OBJECTS = {
    TelemetrySemantic.ANGLES: _ReadObject(REG_ANGLES, decode_i16_vector),
    TelemetrySemantic.MEASURED_SPEEDS: _ReadObject(
        REG_MEASURED_SPEEDS,
        decode_i16_vector,
    ),
    TelemetrySemantic.CONFIGURED_SPEEDS: _ReadObject(
        REG_CONFIGURED_SPEEDS,
        decode_i16_vector,
        DataOrigin.DEVICE_CONFIG_RESPONSE,
    ),
    TelemetrySemantic.EFFORT_LIMITS: _ReadObject(
        REG_EFFORT_LIMITS,
        decode_u16_vector,
        DataOrigin.DEVICE_CONFIG_RESPONSE,
    ),
    TelemetrySemantic.CURRENTS: _ReadObject(REG_CURRENTS, decode_i16_vector),
    TelemetrySemantic.TEMPERATURES: _ReadObject(
        REG_TEMPERATURES,
        decode_u8_vector,
    ),
    TelemetrySemantic.FAULTS: _ReadObject(REG_FAULTS, decode_u8_vector),
}
_BASELINE = (
    TelemetrySemantic.ANGLES,
    TelemetrySemantic.FAULTS,
    TelemetrySemantic.EFFORT_LIMITS,
)
_READ_REGISTERS = (
    REG_DEVICE_INFO,
    REG_FAULTS,
    REG_ANGLES,
    REG_MEASURED_SPEEDS,
    REG_CONFIGURED_SPEEDS,
    REG_EFFORT_LIMITS,
    *range(REG_TACTILE_FIRST, REG_TACTILE_LAST + 1),
    REG_TEMPERATURES,
    REG_CURRENTS,
)


class O20ProtocolBackend:
    """Stateless O20 register transaction profile for one physical side."""

    def __init__(self, side: HandSide) -> None:
        self._device_id = device_id_for_side(side)
        self._endpoint_id = ("o20", self._device_id)
        self._route_keys = tuple(
            read_route(self._device_id, register) for register in _READ_REGISTERS
        )

    @property
    def endpoint_id(self) -> tuple[str, int]:
        """Return the transport-arbitration endpoint identity."""

        return self._endpoint_id

    @property
    def route_keys(self) -> tuple[CANRouteKey, ...]:
        """Return every exact O20 register response route used by this profile."""

        return self._route_keys

    def identity_transaction(
        self,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build a fixed-width DeviceInfo transaction."""

        return o20_identity_transaction(capabilities)

    def baseline_semantics(
        self,
        capabilities: DeviceCapabilities,
    ) -> tuple[TelemetrySemantic, ...]:
        """Return angle, fault, and effort state required before admission."""

        for semantic in _BASELINE:
            if capabilities.telemetry_channel(semantic) is None:
                raise ProtocolError(
                    f"O20 baseline semantic {semantic.value} is not declared"
                )
        return _BASELINE

    def command_transaction(
        self,
        semantic: CommandSemantic,
        values: tuple[RawNumber, ...],
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Encode one raw command without claiming an application ACK."""

        command = capabilities.command(semantic)
        if command is None:
            raise UnsupportedOperationError(f"O20 does not support {semantic.value}")
        try:
            profile = _COMMAND_OBJECTS[semantic]
        except KeyError as error:
            raise UnsupportedOperationError(
                f"O20 has no protocol command for {semantic.value}"
            ) from error
        raw = _validated_command_values(values, semantic, capabilities)
        return ProtocolTransaction(
            key=("command", semantic),
            correlation_key=("o20_write", self._device_id, profile.register),
            request_messages=(
                build_write_message(
                    self._device_id,
                    profile.register,
                    profile.encoder(raw),
                    dlc=profile.dlc,
                ),
            ),
            response_timeout_s=0,
            estimated_duration_s=0.001,
        )

    def read_transaction(
        self,
        semantic: TelemetrySemantic,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build one fresh register or complete tactile read transaction."""

        telemetry = capabilities.telemetry_channel(semantic)
        if telemetry is None or not telemetry.device_queried:
            raise UnsupportedOperationError(
                f"O20 has no active query for {semantic.value}"
            )
        if semantic is TelemetrySemantic.FORCE:
            return tactile_transaction(capabilities, device_id=self._device_id)
        try:
            profile = _READ_OBJECTS[semantic]
        except KeyError as error:
            raise UnsupportedOperationError(
                f"O20 has no protocol read for {semantic.value}"
            ) from error

        def consume(message: object) -> ResponseProgress:
            matched = matching_read_data(
                message,
                device_id=self._device_id,
                register=profile.register,
            )
            if matched is None:
                return ResponseProgress(matched=False)
            raw = profile.decoder(matched[1])
            return ResponseProgress(
                matched=True,
                complete=True,
                result=SamplePayload(
                    semantic=semantic,
                    raw=raw,
                    origin=profile.origin,
                    within_declared_range=_within_declared_range(
                        raw,
                        semantic,
                        capabilities,
                    ),
                ),
            )

        return ProtocolTransaction(
            key=("read", semantic),
            correlation_key=("o20_read", self._device_id, profile.register),
            request_messages=(build_read_message(self._device_id, profile.register),),
            response_timeout_s=0.1,
            estimated_duration_s=telemetry.estimated_transaction_s,
            response_consumer=consume,
            late_response_guard_s=0.005,
        )

    def operation_transaction(
        self,
        operation: str,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Encode the supported bounded clear-fault operation."""

        if operation != "clear_faults" or not capabilities.operations.clear_faults:
            raise UnsupportedOperationError(f"O20 does not support {operation}")
        return ProtocolTransaction(
            key=("operation", operation),
            correlation_key=("o20_write", self._device_id, REG_FAULTS),
            request_messages=(
                build_write_message(
                    self._device_id,
                    REG_FAULTS,
                    encode_zero_faults(),
                    dlc=11,
                ),
            ),
            response_timeout_s=0,
            estimated_duration_s=0.001,
        )

    def decode_report(
        self,
        message: object,
        capabilities: DeviceCapabilities,
    ) -> tuple[SamplePayload, ...]:
        """Ignore unmatched traffic because O20 state is actively queried."""

        del message, capabilities
        return ()


def _within_declared_range(
    raw: tuple[int, ...],
    semantic: TelemetrySemantic,
    capabilities: DeviceCapabilities,
) -> bool:
    if semantic is TelemetrySemantic.ANGLES:
        return all(
            joint.raw_angle_range.contains(value)
            for joint, value in zip(capabilities.joints, raw, strict=True)
        )
    telemetry = capabilities.telemetry_channel(semantic)
    return (
        telemetry is None
        or telemetry.raw_range is None
        or all(telemetry.raw_range.contains(value) for value in raw)
    )


def _validated_command_values(
    values: tuple[RawNumber, ...],
    semantic: CommandSemantic,
    capabilities: DeviceCapabilities,
) -> tuple[RawNumber, ...]:
    command = capabilities.command(semantic)
    if command is None:
        raise UnsupportedOperationError(f"O20 does not support {semantic.value}")
    if len(values) != len(command.channels) or any(
        type(value) is not int for value in values
    ):
        raise ProtocolError(
            f"O20 {semantic.value} must contain {len(command.channels)} integers"
        )
    if any(
        not command.range_for(index).contains(value)
        for index, value in enumerate(values)
    ):
        raise ProtocolError(f"O20 {semantic.value} exceed declared ranges")
    return values


__all__ = ["O20ProtocolBackend"]
