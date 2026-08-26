"""Position control and sensing for the 20 physical O30i joints."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from linkerbot.hand.hand_protocol_v1 import HandProtocolV1
from linkerbot_transport.relay import DataRelay

from . import protocol
from ._runtime import read_vector, write_vector
from .joints import O30iAngle, validate_u8_values


@dataclass(frozen=True, slots=True)
class O30iAngleData:
    """O30i decoded unsigned-byte protocol positions."""

    angles: tuple[int, ...]
    timestamp: float


class AngleManager:
    """Manage measured object ``0x01`` position reads and safe writes.

    Complete writes are split around unimplemented firmware slots so only the
    20 physical joints are changed.
    """

    def __init__(self, client: HandProtocolV1) -> None:
        self._client = client
        self._relay = DataRelay[O30iAngleData]()

    def set_angles(
        self,
        angles: list[int] | tuple[int, ...],
        *,
        timeout_ms: float = 100,
    ) -> None:
        """Set all physical joints from protocol-native bytes."""
        normalized = validate_u8_values(angles, name="angles")
        write_vector(
            self._client,
            main_index=protocol.O30I_MI_POSITION,
            values=normalized,
            timeout_ms=timeout_ms,
        )

    def set_percentage_angles(
        self,
        angles: O30iAngle | list[float] | tuple[float, ...],
        *,
        timeout_ms: float = 100,
    ) -> None:
        """Convert explicit 0..100 targets and send protocol values."""
        percentages = (
            angles if isinstance(angles, O30iAngle) else O30iAngle.from_list(angles)
        )
        self.set_angles(percentages.to_raw(), timeout_ms=timeout_ms)

    def set_slice(
        self,
        offset: int,
        values: list[int] | tuple[int, ...],
        *,
        timeout_ms: float = 100,
    ) -> None:
        """Write a slice in public joint order, splitting wire gaps safely."""
        for sub_index, payload in protocol.encode_control_slice(
            offset, values, name="positions"
        ):
            self._client.write(
                main_index=protocol.O30I_MI_POSITION,
                sub_index=sub_index,
                payload=payload,
                timeout_ms=timeout_ms,
            )

    def set_sparse(
        self,
        values: Mapping[int, int],
        *,
        timeout_ms: float = 100,
    ) -> None:
        """Set selected physical-joint indices through sparse object ``0x30``.

        Keys use the same 0..19 order as :data:`O30I_JOINT_SPECS`. The SDK
        translates them to object 0x30's distinct finger-major joint IDs.
        """
        payload = protocol.encode_sparse_positions(values)
        self._client.write(
            main_index=protocol.O30I_MI_SPARSE_POSITION,
            payload=payload,
            timeout_ms=timeout_ms,
        )

    def get_blocking(self, timeout_ms: float = 100) -> O30iAngleData:
        """Read actual positions (RTS=0) and update the snapshot."""
        values = read_vector(
            self._client,
            main_index=protocol.O30I_MI_POSITION,
            timeout_ms=timeout_ms,
        )
        data = O30iAngleData(
            angles=values,
            timestamp=time.time(),
        )
        self._relay.push(data)
        return data

    def get_target_blocking(self, timeout_ms: float = 100) -> tuple[int, ...]:
        """Read position set-points with RTS=1 without changing the snapshot."""
        values = read_vector(
            self._client,
            main_index=protocol.O30I_MI_POSITION,
            timeout_ms=timeout_ms,
            rts=True,
        )
        return values

    def get_i16_blocking(self, timeout_ms: float = 100) -> tuple[int, ...]:
        """Read the only measured-safe full 72-byte little-endian position block."""
        response = self._client.read(
            main_index=protocol.O30I_MI_POSITION_I16,
            length=protocol.O30I_POSITION_I16_BYTE_LENGTH,
            timeout_ms=timeout_ms,
        )
        return protocol.decode_i16_position_block(response)

    def get_snapshot(self) -> O30iAngleData | None:
        return self._relay.snapshot()

    def _send_sense_request(self) -> None:
        self.get_blocking(timeout_ms=100)

    def _set_event_sink(self, sink: Callable[[O30iAngleData], None]) -> None:
        self._relay.set_sink(sink)

    def close(self) -> None:
        """Release manager resources; this manager owns no threads."""
