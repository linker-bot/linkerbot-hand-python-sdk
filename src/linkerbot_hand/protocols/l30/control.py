"""L30 dispatch-only motion and configuration command transactions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ...api.enums import DataOrigin
from ...api.errors import ProtocolError, UnsupportedOperationError
from ...api.readings import RawNumber
from ...capabilities.types import (
    CommandSemantic,
    DeviceCapabilities,
    TelemetrySemantic,
)
from ..base import ProtocolTransaction, SamplePayload
from .codec import (
    PARENT_CONTROL,
    SUB_CURRENT_LIMIT,
    SUB_POSITION,
    SUB_SPEED,
    encode_i16_vector,
    encode_u16_vector,
)
from .framing import build_request

_Encoder = Callable[[tuple[RawNumber, ...]], bytes]


@dataclass(frozen=True, slots=True)
class _CommandObject:
    subcommand: int
    encoder: _Encoder
    cache_semantic: TelemetrySemantic | None = None


_COMMAND_OBJECTS = {
    CommandSemantic.ANGLES: _CommandObject(SUB_POSITION, encode_i16_vector),
    CommandSemantic.SPEEDS: _CommandObject(
        SUB_SPEED,
        encode_u16_vector,
        TelemetrySemantic.CONFIGURED_SPEEDS,
    ),
    CommandSemantic.CURRENT_LIMITS: _CommandObject(
        SUB_CURRENT_LIMIT,
        encode_u16_vector,
        TelemetrySemantic.CURRENT_LIMITS,
    ),
}


def command_transaction(
    semantic: CommandSemantic,
    values: tuple[RawNumber, ...],
    capabilities: DeviceCapabilities,
    *,
    node_id: int,
    host_id: int,
) -> ProtocolTransaction:
    """Encode one validated command and its optional dispatch-time cache."""

    try:
        profile = _COMMAND_OBJECTS[semantic]
    except KeyError as error:
        raise UnsupportedOperationError(
            f"L30 has no protocol command for {semantic.value}"
        ) from error
    raw = _validated_values(values, semantic, capabilities)
    publications = ()
    if profile.cache_semantic is not None:
        publications = (
            SamplePayload(
                semantic=profile.cache_semantic,
                raw=raw,
                origin=DataOrigin.COMMAND_CACHE,
            ),
        )
    return ProtocolTransaction(
        key=("command", semantic),
        correlation_key=("l30_write", node_id, PARENT_CONTROL, profile.subcommand),
        request_messages=(
            build_request(
                parent=PARENT_CONTROL,
                subcommand=profile.subcommand,
                write=True,
                payload=profile.encoder(raw),
                dlc=0x0E,
                node_id=node_id,
                host_id=host_id,
            ),
        ),
        response_timeout_s=0,
        estimated_duration_s=0.001,
        dispatch_publications=publications,
    )


def _validated_values(
    values: tuple[RawNumber, ...],
    semantic: CommandSemantic,
    capabilities: DeviceCapabilities,
) -> tuple[RawNumber, ...]:
    command = capabilities.command(semantic)
    if command is None:
        raise UnsupportedOperationError(f"L30 does not support {semantic.value}")
    if len(values) != len(command.channels) or any(
        type(value) is not int for value in values
    ):
        raise ProtocolError(
            f"L30 {semantic.value} must contain {len(command.channels)} integers"
        )
    if any(
        not command.range_for(index).contains(value)
        for index, value in enumerate(values)
    ):
        raise ProtocolError(f"L30 {semantic.value} exceed declared ranges")
    return values


__all__ = ["command_transaction"]
