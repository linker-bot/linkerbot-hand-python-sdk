"""Frame layouts and atomic assembly for classic CAN multi-axis objects."""

from __future__ import annotations

from dataclasses import dataclass

import can

from linkerbot_transport import CANRouteKey

from ...api.errors import ProtocolError, ValidationError
from .classic_can import build_classic_message, matching_data


@dataclass(frozen=True, slots=True)
class ClassicFrameLayout:
    """One opcode and its public-channel indices in wire-slot order."""

    opcode: int
    slots: tuple[int | None, ...]

    def __post_init__(self) -> None:
        if type(self.opcode) is not int or not 0 <= self.opcode <= 0xFF:
            raise ValidationError("classic frame opcode must be a protocol byte")
        if (
            not isinstance(self.slots, tuple)
            or not self.slots
            or len(self.slots) > 7
            or any(
                value is not None and (type(value) is not int or value < 0)
                for value in self.slots
            )
        ):
            raise ValidationError(
                "classic frame slots must contain non-negative indices or None"
            )


@dataclass(frozen=True, slots=True)
class ClassicObjectLayout:
    """Complete frame layout for one public multi-channel protocol object."""

    channel_count: int
    frames: tuple[ClassicFrameLayout, ...]

    def __post_init__(self) -> None:
        if type(self.channel_count) is not int or self.channel_count <= 0:
            raise ValidationError("classic object channel_count must be positive")
        if not isinstance(self.frames, tuple) or not self.frames:
            raise ValidationError("classic object must contain frames")
        if any(not isinstance(value, ClassicFrameLayout) for value in self.frames):
            raise ValidationError("frames must contain ClassicFrameLayout values")
        if len({value.opcode for value in self.frames}) != len(self.frames):
            raise ValidationError("classic object opcodes must be unique")
        indices = tuple(
            slot for frame in self.frames for slot in frame.slots if slot is not None
        )
        if tuple(sorted(indices)) != tuple(range(self.channel_count)):
            raise ValidationError(
                "classic object must map every public channel exactly once"
            )

    @property
    def opcodes(self) -> tuple[int, ...]:
        """Return frame opcodes in dispatch order."""

        return tuple(value.opcode for value in self.frames)

    def encode(
        self, values: tuple[int, ...], route: CANRouteKey
    ) -> tuple[can.Message, ...]:
        """Encode public-channel values and zero-fill reserved wire slots."""

        if len(values) != self.channel_count:
            raise ProtocolError(
                f"classic object requires {self.channel_count} protocol values"
            )
        return tuple(
            build_classic_message(
                route,
                bytes(
                    (frame.opcode,)
                    + tuple(0 if slot is None else values[slot] for slot in frame.slots)
                ),
            )
            for frame in self.frames
        )

    def query(self, route: CANRouteKey) -> tuple[can.Message, ...]:
        """Build one query frame for every fragment of this object."""

        return tuple(
            build_classic_message(route, bytes((frame.opcode,)))
            for frame in self.frames
        )


class ClassicObjectAssembler:
    """Assemble one multi-frame object without publishing partial state."""

    def __init__(self, *, route: CANRouteKey, layout: ClassicObjectLayout) -> None:
        self._route = route
        self._layout = layout
        self._frame_by_opcode = {value.opcode: value for value in layout.frames}
        self._values: dict[int, tuple[int, ...]] = {}

    def offer(self, message: object) -> tuple[bool, tuple[int, ...] | None]:
        """Consume one matching fragment and return a completed public vector."""

        data = matching_data(message, self._route)
        if data is None or not data:
            return False, None
        frame = self._frame_by_opcode.get(data[0])
        if frame is None:
            return False, None
        expected = len(frame.slots) + 1
        if len(data) != expected:
            raise ProtocolError(
                f"opcode 0x{frame.opcode:02X} response must contain {expected} bytes"
            )
        payload = tuple(data[1:])
        previous = self._values.get(frame.opcode)
        if previous is not None and previous != payload:
            raise ProtocolError("classic object fragment conflicts with a duplicate")
        self._values[frame.opcode] = payload
        if len(self._values) != len(self._layout.frames):
            return True, None
        result = [0] * self._layout.channel_count
        for item in self._layout.frames:
            for slot, value in zip(item.slots, self._values[item.opcode], strict=True):
                if slot is not None:
                    result[slot] = value
        return True, tuple(result)


__all__ = [
    "ClassicFrameLayout",
    "ClassicObjectAssembler",
    "ClassicObjectLayout",
]
