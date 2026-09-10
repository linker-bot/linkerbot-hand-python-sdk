"""Range-aware HOP response assembly and device-error classification."""

from __future__ import annotations

from ...api.errors import (
    DeviceFaultError,
    ProtocolError,
    UnsupportedFirmwareError,
)
from .framing import ERROR_MAIN_INDEX, matching_response_frame

_DEVICE_ERROR_MESSAGES = {
    0x02: "sub-index does not exist or is out of range",
    0x40: "illegal data value",
}


class HOPRangeAssembler:
    """Assemble one serialized HOP object range by absolute sub-index."""

    def __init__(
        self,
        main_index: int,
        *,
        sub_index: int,
        length: int,
        rts: bool,
        response_id: int,
        object_unavailable_is_unsupported: bool = False,
    ) -> None:
        self._main_index = main_index
        self._sub_index = sub_index
        self._length = length
        self._rts = rts
        self._response_id = response_id
        self._object_unavailable_is_unsupported = object_unavailable_is_unsupported
        self._buffer = bytearray(length)
        self._received = bytearray(length)

    def consume(self, message: object) -> tuple[bool, bytes | None]:
        """Return ``(matched, complete_payload)`` for one routed message."""

        frame = matching_response_frame(message, self._response_id)
        if frame is None:
            return False, None
        if (
            frame.main_index == ERROR_MAIN_INDEX
            and self._main_index != ERROR_MAIN_INDEX
        ):
            if len(frame.payload) != 1:
                raise ProtocolError("O30 HOP error response must contain one byte")
            code = frame.payload[0]
            description = _DEVICE_ERROR_MESSAGES.get(code, "unknown device error")
            if code == 0x02 and self._object_unavailable_is_unsupported:
                raise UnsupportedFirmwareError(
                    f"O30 HOP object 0x{self._main_index:02X}/"
                    f"0x{self._sub_index:02X} is unavailable: {description}"
                )
            raise DeviceFaultError(
                f"O30 HOP error 0x{code:02X} at index "
                f"0x{frame.sub_index:02X}: {description}"
            )
        if frame.main_index != self._main_index or frame.rts != self._rts:
            return False, None
        if not frame.payload:
            raise ProtocolError("O30 HOP response fragment is empty")
        start = frame.sub_index - self._sub_index
        end = start + len(frame.payload)
        if start < 0 or end > self._length:
            return False, None
        for offset, value in enumerate(frame.payload, start=start):
            if self._received[offset] and self._buffer[offset] != value:
                raise ProtocolError("O30 HOP duplicate fragment conflicts")
            self._buffer[offset] = value
            self._received[offset] = 1
        if not all(self._received):
            return True, None
        return True, bytes(self._buffer)


__all__ = ["HOPRangeAssembler"]
