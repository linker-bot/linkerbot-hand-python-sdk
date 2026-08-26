"""Angle control and sensing for L20lite robotic hand.

This module provides the AngleManager class for controlling joint angles
and reading angle sensor data via CAN bus communication.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

import can

from linkerbot.hand._can_protocol import CANDispatcherLike
from linkerbot.hand._polling_relay import PollingDataRelay
from linkerbot.hand._values import percentage_to_u8_vector, validate_u8_vector
from linkerbot_transport.errors import ValidationError

_JOINT_COUNT = 10
_JOINT_NAMES = [
    "thumb_flex",
    "thumb_abd",
    "index_flex",
    "middle_flex",
    "ring_flex",
    "pinky_flex",
    "index_abd",
    "ring_abd",
    "pinky_abd",
    "thumb_yaw",
]


@dataclass
class L20liteAngle:
    """Joint angles for L20lite hand (0-100 range).

    Attributes:
        thumb_flex: Thumb flexion joint angle (0-100)
        thumb_abd: Thumb abduction joint angle (0-100)
        index_flex: Index finger flexion joint angle (0-100)
        middle_flex: Middle finger flexion joint angle (0-100)
        ring_flex: Ring finger flexion joint angle (0-100)
        pinky_flex: Pinky finger flexion joint angle (0-100)
        index_abd: Index finger abduction joint angle (0-100)
        ring_abd: Ring finger abduction joint angle (0-100)
        pinky_abd: Pinky finger abduction joint angle (0-100)
        thumb_yaw: Thumb yaw joint angle (0-100)
    """

    thumb_flex: float
    thumb_abd: float
    index_flex: float
    middle_flex: float
    ring_flex: float
    pinky_flex: float
    index_abd: float
    ring_abd: float
    pinky_abd: float
    thumb_yaw: float

    def to_list(self) -> list[float]:
        """Convert to list of floats in joint order.

        Returns:
            List of 10 joint angles in order: thumb_flex, thumb_abd,
            index_flex, middle_flex, ring_flex, pinky_flex,
            index_abd, ring_abd, pinky_abd, thumb_yaw
        """
        return [
            self.thumb_flex,
            self.thumb_abd,
            self.index_flex,
            self.middle_flex,
            self.ring_flex,
            self.pinky_flex,
            self.index_abd,
            self.ring_abd,
            self.pinky_abd,
            self.thumb_yaw,
        ]

    @classmethod
    def from_list(cls, values: list[float]) -> "L20liteAngle":
        """Construct from list of floats (0-100 range).

        Args:
            values: List of 10 float values in 0-100 range

        Returns:
            L20liteAngle instance

        Raises:
            ValueError: If list doesn't have exactly 10 elements
        """
        if len(values) != _JOINT_COUNT:
            raise ValueError(f"Expected {_JOINT_COUNT} values, got {len(values)}")
        for value in values:
            if not isinstance(value, (float, int)):
                raise ValueError(f"Angle value {value} must be float/int")
            if not 0 <= value <= 100:
                raise ValueError(f"Angle value {value} out of range [0, 100]")
        return cls(
            thumb_flex=values[0],
            thumb_abd=values[1],
            index_flex=values[2],
            middle_flex=values[3],
            ring_flex=values[4],
            pinky_flex=values[5],
            index_abd=values[6],
            ring_abd=values[7],
            pinky_abd=values[8],
            thumb_yaw=values[9],
        )

    def __getitem__(self, index: int) -> float:
        """Support indexing: angles[0] returns thumb_flex.

        Args:
            index: Joint index (0-9)

        Returns:
            Joint angle value

        Raises:
            IndexError: If index is out of range
        """
        return self.to_list()[index]

    def __len__(self) -> int:
        """Return number of joints (always 10 for L20lite)."""
        return _JOINT_COUNT


@dataclass(frozen=True)
class AngleData:
    """Immutable angle data container.

    Attributes:
        angles: Ten protocol-native unsigned-byte joint positions.
        timestamp: Unix timestamp when the data was received.
    """

    angles: tuple[int, ...]
    timestamp: float


class AngleManager:
    """Manager for joint angle control and sensing.

    This class provides three access modes for angle operations:
    1. Angle control: set_angles() - send 10 target angles to the hand
    2. Blocking mode: get_blocking() - request and wait for all 10 current angles
    3. Cache reading: get_snapshot() - non-blocking read of cached angles
    """

    _FRAME_MAP: dict[int, list[str]] = {
        0x01: [
            "thumb_flex",
            "thumb_abd",
            "index_flex",
            "middle_flex",
            "ring_flex",
            "pinky_flex",
        ],
        0x04: ["index_abd", "ring_abd", "pinky_abd", "thumb_yaw"],
    }

    def __init__(
        self,
        arbitration_id: int,
        dispatcher: CANDispatcherLike,
    ) -> None:
        """Initialize the angle manager.

        Args:
            arbitration_id: CAN arbitration ID for angle control/sensing.
            dispatcher: CAN message dispatcher to use for communication.
        """
        self._arbitration_id = arbitration_id
        self._dispatcher = dispatcher
        self._pending: dict[int, list[int]] = {}
        self._relay = PollingDataRelay[AngleData](self._pending.clear)
        self._dispatcher.subscribe(self._on_message)

    def set_angles(self, angles: list[int] | tuple[int, ...]) -> None:
        """Send protocol-native unsigned-byte target positions.

        This method sends 10 target angles to the hand. The hand
        will respond with the current angles, which are automatically cached
        and can be retrieved via get_snapshot().

        Args:
            angles: Ten integer protocol values in the range 0..255.

        Raises:
            ValidationError: If angles count is not 10 or values are out of range.

        Use :meth:`set_percentage_angles` for explicit 0..100 targets.
        """
        raw_angles = validate_u8_vector(angles, count=_JOINT_COUNT, name="angles")
        raw_by_field = dict(zip(_JOINT_NAMES, raw_angles, strict=True))
        for cmd, fields in self._FRAME_MAP.items():
            data = [cmd, *[raw_by_field[f] for f in fields]]
            msg = can.Message(
                arbitration_id=self._arbitration_id,
                data=data,
                is_extended_id=False,
            )
            time.sleep(0.0005)
            self._dispatcher.send(msg)

    def set_percentage_angles(
        self, angles: L20liteAngle | list[float] | tuple[float, ...]
    ) -> None:
        """Convert explicit 0..100 targets and send protocol values."""
        values = angles.to_list() if isinstance(angles, L20liteAngle) else angles
        self.set_angles(
            percentage_to_u8_vector(
                values, count=_JOINT_COUNT, name="angle percentages"
            )
        )

    def get_blocking(self, timeout_ms: float = 100) -> AngleData:
        """Request and wait for current joint angles (blocking).

        This method sends a sensing request and blocks until
        all 10 current angles are received or the timeout expires.

        Args:
            timeout_ms: Maximum time to wait in milliseconds (default: 100).

        Returns:
            AngleData instance containing angles and timestamp.

        Raises:
            TimeoutError: If no response is received within timeout.
            ValidationError: If timeout_ms is not positive.

        Example:
            >>> data = manager.get_blocking(timeout_ms=500)
            >>> print(f"Current angles: {data.angles}")
        """
        if timeout_ms <= 0:
            raise ValidationError("timeout_ms must be positive")
        return self._relay.request(self._send_request_frames, timeout_ms / 1000.0)

    def get_snapshot(self) -> AngleData | None:
        """Get the most recent cached angle data (non-blocking).

        Returns:
            AngleData instance or None if no data received yet.

        Example:
            >>> data = manager.get_snapshot()
            >>> if data:
            ...     print(f"Fresh angles: {data.angles}")
        """
        return self._relay.snapshot()

    def _set_event_sink(self, sink: Callable[[AngleData], None]) -> None:
        self._relay.set_sink(sink)

    def _send_sense_request(self) -> None:
        self._relay.poll(self._send_request_frames)

    def _cancel_sense_request(self) -> None:
        self._relay.cancel_polling()

    def _send_request_frames(self) -> None:
        for cmd in self._FRAME_MAP:
            msg = can.Message(
                arbitration_id=self._arbitration_id,
                data=[cmd],
                is_extended_id=False,
            )
            time.sleep(0.0005)
            self._dispatcher.send(msg)

    def _on_message(self, msg: can.Message) -> None:
        if msg.arbitration_id != self._arbitration_id:
            return

        if len(msg.data) < 2:
            return

        cmd = msg.data[0]
        if cmd not in self._FRAME_MAP:
            return

        expected_fields = self._FRAME_MAP[cmd]
        raw_angles = list(msg.data[1:])

        if len(raw_angles) != len(expected_fields):
            return

        self._pending[cmd] = raw_angles

        # Check if all frames have been received
        if set(self._pending.keys()) != set(self._FRAME_MAP.keys()):
            return

        # All frames received — merge in documented joint order.
        hardware_by_field: dict[str, int] = {}
        for frame_cmd, fields in self._FRAME_MAP.items():
            for field, value in zip(fields, self._pending[frame_cmd], strict=True):
                hardware_by_field[field] = value

        hardware_raw_angles = [hardware_by_field[name] for name in _JOINT_NAMES]
        angle_data = AngleData(
            angles=tuple(hardware_raw_angles),
            timestamp=time.time(),
        )
        self._relay.push(angle_data)
