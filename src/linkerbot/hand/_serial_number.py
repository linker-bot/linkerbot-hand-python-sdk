"""Shared assembly for classic CAN serial-number responses."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import ClassVar

from linkerbot_transport.errors import TimeoutError


@dataclass(frozen=True)
class SerialNumberFrames:
    """Collect serial-number fragments using frame numbers or byte offsets."""

    _FRAME_SIZE: ClassVar[int] = 6
    _FULL_OFFSETS: ClassVar[tuple[int, ...]] = (0, 6, 12, 18)
    _MIN_SHORT_FRAMES: ClassVar[int] = 3

    frames: Mapping[int, bytes] = field(default_factory=dict)
    terminal_offset: int | None = None

    def add_frame(self, position: int, data: bytes) -> SerialNumberFrames:
        """Return a copy containing one normalized response fragment.

        Classic firmware variants encode ``position`` either as a sequential
        frame number (0, 1, 2, 3) or as a byte offset (0, 6, 12, 18).
        Unknown positions are ignored so malformed CAN traffic cannot complete
        the active identity transaction.

        Args:
            position: Protocol frame number or byte offset.
            data: Up to six serial-number bytes.

        Returns:
            Immutable fragment collection with canonical byte-offset keys.
        """
        offset = self._normalize_offset(position)
        if offset is None:
            return self
        payload = bytes(data[: self._FRAME_SIZE])
        frames = {**self.frames, offset: payload}
        terminal_offset = self.terminal_offset
        if len(payload) < self._FRAME_SIZE or b"\x00" in payload:
            terminal_offset = offset
        return SerialNumberFrames(
            frames=frames,
            terminal_offset=terminal_offset,
        )

    def is_complete(self) -> bool:
        """Return whether an explicit terminal fragment has been received."""
        offsets = self._contiguous_offsets()
        if offsets == self._FULL_OFFSETS:
            return True
        return (
            self.terminal_offset is not None
            and offsets
            == self._FULL_OFFSETS[: self.terminal_offset // self._FRAME_SIZE + 1]
        )

    def is_short_complete(self) -> bool:
        """Return whether timeout may terminate a contiguous legacy response."""
        offsets = self._contiguous_offsets()
        return (
            self._MIN_SHORT_FRAMES <= len(offsets) < len(self._FULL_OFFSETS)
            and offsets == self._FULL_OFFSETS[: len(offsets)]
        )

    def assemble(self) -> str:
        """Decode all contiguous fragments from byte offset zero."""
        offsets = self._contiguous_offsets()
        if not offsets or offsets != self._FULL_OFFSETS[: len(offsets)]:
            raise ValueError("serial-number fragments are not contiguous")
        data = b"".join(self.frames[offset] for offset in offsets)
        return data.rstrip(b"\x00").decode("ascii", errors="ignore")

    def _contiguous_offsets(self) -> tuple[int, ...]:
        """Return known fragment offsets in protocol order."""
        return tuple(offset for offset in self._FULL_OFFSETS if offset in self.frames)

    @classmethod
    def _normalize_offset(cls, position: int) -> int | None:
        """Map either supported position encoding to a byte offset."""
        if 0 <= position < len(cls._FULL_OFFSETS):
            return position * cls._FRAME_SIZE
        if position in cls._FULL_OFFSETS:
            return position
        return None


class SerialNumberCollector:
    """Synchronize one classic serial-number request with receive callbacks."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._request_lock = threading.Lock()
        self._active = False
        self._frames = SerialNumberFrames()

    def request(self, send: Callable[[], object], timeout_s: float) -> str:
        """Start, send and wait for one serialized serial-number transaction."""

        with self._request_lock:
            self.begin()
            completed = False
            try:
                send()
                serial_number = self.wait(timeout_s)
                completed = True
                return serial_number
            finally:
                if not completed:
                    self.cancel()

    def begin(self) -> None:
        """Start a request and discard fragments from any earlier transaction."""
        with self._condition:
            self._frames = SerialNumberFrames()
            self._active = True
            self._condition.notify_all()

    def cancel(self) -> None:
        """Cancel the active request and discard its fragments."""
        with self._condition:
            self._active = False
            self._frames = SerialNumberFrames()
            self._condition.notify_all()

    def add_frame(self, position: int, data: bytes) -> None:
        """Add one response fragment when a request is active."""
        with self._condition:
            if not self._active:
                return
            self._frames = self._frames.add_frame(position, data)
            self._condition.notify_all()

    def wait(self, timeout_s: float) -> str:
        """Wait for a complete response or finalize a contiguous short reply.

        Some classic L6 firmware returns a three-fragment, 18-byte serial number
        without a terminator. Such a response is only accepted after the full
        wait window expires. Missing middle fragments remain a timeout.

        Args:
            timeout_s: Maximum response wait in seconds.

        Returns:
            Decoded serial number.

        Raises:
            TimeoutError: If no complete or valid short response is available.
        """
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while self._active:
                if self._frames.is_complete():
                    return self._finish_locked()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if self._frames.is_short_complete():
                        return self._finish_locked()
                    break
                self._condition.wait(remaining)
            self._active = False
            self._frames = SerialNumberFrames()
        raise TimeoutError(f"No data received within {timeout_s * 1000:.0f}ms")

    def _finish_locked(self) -> str:
        """Assemble and clear the active transaction while holding the lock."""
        serial_number = self._frames.assemble()
        self._active = False
        self._frames = SerialNumberFrames()
        return serial_number


__all__ = ["SerialNumberCollector", "SerialNumberFrames"]
