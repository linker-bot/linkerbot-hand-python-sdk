"""O20 protocol-native position control and sensing."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from linkerbot_transport.errors import ValidationError
from linkerbot_transport.relay import DataRelay

from . import protocol
from .client import O20Client
from .joints import O20Angle, validate_raw_command_values


@dataclass(frozen=True, slots=True)
class O20AngleData:
    """Immutable O20 protocol position data.

    Attributes:
        angles: 16 decoded signed protocol values in motor-ID order, in degrees.
        timestamp: Unix timestamp when the data was decoded.
    """

    angles: tuple[int, ...]
    timestamp: float


class AngleManager:
    """Manage O20 protocol-native target and measured joint positions."""

    def __init__(self, client: O20Client) -> None:
        """Initialize the angle manager.

        Args:
            client: O20 protocol client used for register read/write commands.
        """
        self._client = client
        self._relay = DataRelay[O20AngleData]()

    def set_angles(self, angles: list[int] | tuple[int, ...]) -> None:
        """Send 16 protocol-native target positions in degrees.

        Args:
            angles: 16 protocol integers in motor-ID order. Each value must be
                within the corresponding :data:`O20_JOINT_SPECS` range.

        Raises:
            ValidationError: If count, type, or a per-joint range is invalid.
        """
        validated = validate_raw_command_values(angles)
        self._send_angles(list(validated))

    def set_percentage_angles(self, angles: O20Angle | Sequence[int | float]) -> None:
        """Convert explicit 0..100 targets and send protocol values."""
        percentages = (
            angles if isinstance(angles, O20Angle) else O20Angle.from_list(angles)
        )
        self.set_angles(percentages.to_raw())

    def get_blocking(self, timeout_ms: float = 100) -> O20AngleData:
        """Read current joint angles and update the cached snapshot.

        Args:
            timeout_ms: Time to wait for the read response.

        Returns:
            O20AngleData containing decoded signed protocol values in degrees.

        Raises:
            ValidationError: If timeout_ms is not positive.
            TimeoutError: If no matching response arrives before the timeout.
            ProtocolError: If the response payload is shorter than expected.
        """
        if timeout_ms <= 0:
            raise ValidationError("timeout_ms must be positive")
        response = self._client.read(
            register=protocol.O20_REG_CURRENT_POS, timeout_ms=timeout_ms
        )
        data = O20AngleData(
            angles=protocol.decode_i16_vector_response(response),
            timestamp=time.time(),
        )
        self._relay.push(data)
        return data

    def get_snapshot(self) -> O20AngleData | None:
        """Return the latest cached angle data without sending a request.

        Returns:
            Cached O20AngleData, or None if no angle data has been received yet.
        """
        return self._relay.snapshot()

    def _send_angles(self, values: list[int]) -> None:
        self._client.write(
            register=protocol.O20_REG_TARGET_POS,
            payload=protocol.encode_i16_vector(tuple(values)),
            dlc=protocol.O20_VECTOR_DLC,
        )

    def _send_sense_request(self) -> None:
        self.get_blocking(timeout_ms=100)

    def _set_event_sink(self, sink: Callable[[O20AngleData], None]) -> None:
        self._relay.set_sink(sink)

    def close(self) -> None:
        """Release any per-manager resources. The angle manager has none."""
