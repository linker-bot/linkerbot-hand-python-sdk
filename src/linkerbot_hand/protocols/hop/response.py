"""Range-aware HOP response assembly and device-error classification."""

from __future__ import annotations

from dataclasses import replace

import can

from ...api.errors import ProtocolError, UnsupportedFirmwareError
from ..base import ProtocolTransaction, ResponseProgress
from .framing import (
    ERROR_MAIN_INDEX,
    HOPAddressing,
    build_read_message,
    matching_response_frame,
)

_DEVICE_ERROR_MESSAGES = {
    0x01: "main index does not exist",
    0x02: "sub-index does not exist",
    0x04: "register is not writable",
    0x08: "data length mismatch",
    0x10: "permission denied",
    0x20: "returned data contains zero padding",
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
                raise ProtocolError("HOP error response must contain one byte")
            code = frame.payload[0]
            if code == 0:
                return False, None
            descriptions = [
                message for bit, message in _DEVICE_ERROR_MESSAGES.items() if code & bit
            ]
            if code & ~0x3F:
                descriptions.append(f"unknown flags 0x{code & ~0x3F:02X}")
            description = "; ".join(descriptions)
            if (
                self._object_unavailable_is_unsupported
                and code & 0x03
                and not code & ~0x23
            ):
                raise UnsupportedFirmwareError(
                    f"HOP object 0x{self._main_index:02X}/"
                    f"0x{self._sub_index:02X} is unavailable "
                    f"(error 0x{code:02X}): {description}"
                )
            # 0x4F reports communication flags, not joint/mechanical faults.
            # Its SI is an error-history slot, not the rejected object's SI.
            raise ProtocolError(
                f"HOP communication error 0x{code:02X} at history slot "
                f"0x{frame.sub_index:02X}: {description}"
            )
        if frame.main_index != self._main_index or frame.rts != self._rts:
            return False, None
        if not frame.payload:
            raise ProtocolError("HOP response fragment is empty")
        start = frame.sub_index - self._sub_index
        end = start + len(frame.payload)
        if start < 0 or end > self._length:
            if end > 0 and start < self._length:
                raise ProtocolError(
                    "HOP response fragment overlaps requested range but exceeds it"
                )
            return False, None
        for offset, value in enumerate(frame.payload, start=start):
            if self._received[offset] and self._buffer[offset] != value:
                raise ProtocolError("HOP duplicate fragment conflicts")
            self._buffer[offset] = value
            self._received[offset] = 1
        if not all(self._received):
            return True, None
        return True, bytes(self._buffer)


def serialize_classic_read(
    transaction: ProtocolTransaction, addressing: HOPAddressing
) -> ProtocolTransaction:
    """Gate each five-byte classic read on its reply, reusing the full decoder.

    Busy shared buses can expose firmware that drops burst response fragments.
    Keep the original consumer across stages so only a complete object produces
    a result. FD requests retain their existing framing and transaction shape.
    """
    if addressing.can_fd:
        return transaction
    message = transaction.request_messages[0]
    if not isinstance(message, can.Message) or len(message.data) != 3:
        raise ProtocolError("serialized classic read requires one HOP read request")
    control, start, length = bytes(message.data)
    if length <= 5:
        return transaction
    count = (length + 4) // 5

    def stage(offset: int) -> ProtocolTransaction:
        size = min(5, length - offset)
        final = offset + size == length
        assembler = HOPRangeAssembler(
            control & 0x7F,
            sub_index=start + offset,
            length=size,
            rts=bool(control & 0x80),
            response_id=addressing.response_id,
        )

        def consume(response: object) -> ResponseProgress:
            # Preserve the original object's unsupported/error classification.
            frame = matching_response_frame(response, addressing.response_id)
            if frame is not None and frame.main_index == ERROR_MAIN_INDEX:
                return transaction.consume(response)
            matched, complete = assembler.consume(response)
            if not matched:
                return ResponseProgress(matched=False)
            progress = transaction.consume(response)
            if complete is None:
                return ResponseProgress(matched=matched)
            if final:
                if not progress.complete:
                    raise ProtocolError("serialized HOP read omitted object bytes")
                return progress
            return ResponseProgress(matched=True, complete=True)

        next_factory = None
        if not final:
            next_factory = lambda: stage(offset + size)
        elif (
            transaction.next_transaction is not None
            or transaction.next_transaction_factory is not None
        ):
            next_factory = transaction.build_next_transaction
        return replace(
            transaction,
            request_messages=(
                build_read_message(
                    control & 0x7F,
                    sub_index=start + offset,
                    length=size,
                    request_id=addressing.request_id,
                    rts=bool(control & 0x80),
                    can_fd=False,
                ),
            ),
            estimated_duration_s=transaction.estimated_duration_s / count,
            response_consumer=consume,
            next_transaction=None,
            next_transaction_factory=next_factory,
            yield_after_response=transaction.yield_after_response if final else False,
        )

    return stage(0)


__all__ = ["HOPRangeAssembler", "serialize_classic_read"]
