"""Generation-bound multipart response assembly by index or byte offset."""

from __future__ import annotations

from collections.abc import Hashable
from enum import Enum

from ..api.errors import ProtocolError, ValidationError


class FragmentMarkerMode(str, Enum):
    """Meaning of the marker carried by each multipart response frame."""

    INDEX = "index"
    BYTE_OFFSET = "byte_offset"


class MultipartAssembler:
    """Assemble out-of-order fragments without accepting stale transactions."""

    def __init__(
        self,
        *,
        total_size: int,
        fragment_size: int,
        marker_mode: FragmentMarkerMode,
        generation: int,
        source_id: Hashable,
        transaction_token: Hashable,
    ) -> None:
        for name, value in (
            ("total_size", total_size),
            ("fragment_size", fragment_size),
            ("generation", generation),
        ):
            if type(value) is not int or value <= 0:
                raise ValidationError(f"{name} must be a positive int")
        if not isinstance(marker_mode, FragmentMarkerMode):
            raise ValidationError("marker_mode must be FragmentMarkerMode")
        _require_hashable(source_id, "source_id")
        _require_hashable(transaction_token, "transaction_token")
        self.total_size = total_size
        self.fragment_size = fragment_size
        self.marker_mode = marker_mode
        self.generation = generation
        self.source_id = source_id
        self.transaction_token = transaction_token
        self._data = bytearray(total_size)
        self._received = bytearray(total_size)
        self._received_count = 0
        self._complete = False
        self._aborted = False

    @property
    def complete(self) -> bool:
        """Return whether every byte has been received exactly or identically."""

        return self._complete

    def add(
        self,
        marker: int,
        payload: bytes,
        *,
        generation: int,
        source_id: Hashable,
        transaction_token: Hashable,
    ) -> bool:
        """Add one fragment and return whether the logical response is complete."""

        if self._aborted:
            raise ProtocolError("multipart transaction is no longer active")
        if self._complete:
            raise ProtocolError("multipart transaction is already complete")
        if generation != self.generation:
            raise ProtocolError("multipart fragment belongs to a stale generation")
        if source_id != self.source_id:
            raise ProtocolError("multipart fragment belongs to another device")
        if transaction_token != self.transaction_token:
            raise ProtocolError("multipart fragment belongs to another transaction")
        if type(marker) is not int or marker < 0:
            raise ProtocolError("multipart marker must be a non-negative int")
        if not isinstance(payload, bytes) or not payload:
            raise ProtocolError("multipart payload must be non-empty bytes")
        if len(payload) > self.fragment_size:
            raise ProtocolError("multipart payload exceeds declared fragment size")
        offset = (
            marker * self.fragment_size
            if self.marker_mode is FragmentMarkerMode.INDEX
            else marker
        )
        if offset >= self.total_size or offset + len(payload) > self.total_size:
            raise ProtocolError("multipart fragment is outside the response range")
        for index, value in enumerate(payload, start=offset):
            if self._received[index]:
                if self._data[index] != value:
                    raise ProtocolError("multipart duplicate conflicts with prior data")
                continue
            self._data[index] = value
            self._received[index] = 1
            self._received_count += 1
        self._complete = self._received_count == self.total_size
        return self._complete

    def result(self) -> bytes:
        """Return the complete payload or reject missing fragments."""

        if not self._complete:
            raise ProtocolError("multipart response is incomplete")
        return bytes(self._data)

    def abort(self) -> None:
        """Reject all later fragments after timeout or generation end."""

        self._aborted = True

    def missing_ranges(self) -> tuple[tuple[int, int], ...]:
        """Return half-open byte ranges not yet received for diagnostics."""

        ranges: list[tuple[int, int]] = []
        start: int | None = None
        for index, received in enumerate(self._received):
            if not received and start is None:
                start = index
            elif received and start is not None:
                ranges.append((start, index))
                start = None
        if start is not None:
            ranges.append((start, self.total_size))
        return tuple(ranges)


def _require_hashable(value: object, name: str) -> None:
    try:
        hash(value)
    except TypeError as error:
        raise ValidationError(f"{name} must be hashable") from error


__all__ = ["FragmentMarkerMode", "MultipartAssembler"]
