"""Speed control and sensing for L6 robotic hand.

This module provides the SpeedManager class for controlling motor speeds
and reading speed sensor data via CAN bus communication.
"""

import time
from dataclasses import dataclass

import can

from linkerbot.hand._values import percentage_to_u8_vector, validate_u8_vector
from linkerbot_transport.can import CANMessageDispatcher
from linkerbot_transport.relay import DataRelay


@dataclass
class L6Speed:
    """Motor speeds for L6 hand (0-100 range).

    Attributes:
        thumb_flex: Thumb flexion motor speed (0-100)
        thumb_abd: Thumb abduction motor speed (0-100)
        index: Index finger motor speed (0-100)
        middle: Middle finger motor speed (0-100)
        ring: Ring finger motor speed (0-100)
        pinky: Pinky finger motor speed (0-100)
    """

    thumb_flex: float
    thumb_abd: float
    index: float
    middle: float
    ring: float
    pinky: float

    def to_list(self) -> list[float]:
        """Convert to list of floats in joint order.

        Returns:
            List of 6 motor speeds [thumb_flex, thumb_abd, index, middle, ring, pinky]
        """
        return [
            self.thumb_flex,
            self.thumb_abd,
            self.index,
            self.middle,
            self.ring,
            self.pinky,
        ]

    @classmethod
    def from_list(cls, values: list[float]) -> "L6Speed":
        """Construct from list of floats (0-100 range).

        Args:
            values: List of 6 float values in 0-100 range

        Returns:
            L6Speed instance

        Raises:
            ValueError: If list doesn't have exactly 6 elements
        """
        if len(values) != 6:
            raise ValueError(f"Expected 6 values, got {len(values)}")
        for value in values:
            if not isinstance(value, (float, int)):
                raise ValueError(f"Speed value {value} must be float/int")
            if not 0 <= value <= 100:
                raise ValueError(f"Speed value {value} out of range [0, 100]")
        return cls(
            thumb_flex=values[0],
            thumb_abd=values[1],
            index=values[2],
            middle=values[3],
            ring=values[4],
            pinky=values[5],
        )

    def to_raw(self) -> list[int]:
        # Internal: Convert to hardware communication format
        return [round(v * 255 / 100) for v in self.to_list()]

    @classmethod
    def from_raw(cls, values: list[int]) -> "L6Speed":
        # Internal: Construct from hardware communication format
        if len(values) != 6:
            raise ValueError(f"Expected 6 values, got {len(values)}")
        for value in values:
            if value < 0 or value > 255:
                raise ValueError(f"Value {value} out of range [0, 255]")
        normalized = [v * 100 / 255 for v in values]
        return cls.from_list(normalized)

    def __getitem__(self, index: int) -> float:
        """Support indexing: speeds[0] returns thumb_flex.

        Args:
            index: Joint index (0-5)

        Returns:
            Motor speed value

        Raises:
            IndexError: If index is out of range
        """
        return self.to_list()[index]

    def __len__(self) -> int:
        """Return number of motors (always 6 for L6)."""
        return 6


@dataclass(frozen=True)
class SpeedData:
    """Last L6 speed target set by the host.

    L6 has no protocol query for current motor speed.  This value is therefore
    a target snapshot and must not be interpreted as measured feedback.
    """

    speeds: tuple[int, ...]
    timestamp: float


class SpeedManager:
    """Manager for motor speed control.

    This class handles speed control operations by sending target speeds
    to the robotic hand motors.
    """

    _CONTROL_CMD = 0x05
    _SPEED_COUNT = 6

    def __init__(self, arbitration_id: int, dispatcher: CANMessageDispatcher) -> None:
        """Initialize the speed manager.

        Args:
            arbitration_id: CAN arbitration ID for speed control.
            dispatcher: CAN message dispatcher to use for communication.
        """
        self._arbitration_id = arbitration_id
        self._dispatcher = dispatcher
        self._relay = DataRelay[SpeedData]()

    def set_speeds(self, speeds: list[int] | tuple[int, ...]) -> None:
        """Send protocol-native unsigned-byte speed targets.

        This method sends 6 target speeds to the hand.

        Args:
            speeds: Six integer protocol values in the range 0..255.

        Raises:
            ValidationError: If speeds count is not 6 or values are out of range.

        Use :meth:`set_percentage_speeds` for explicit 0..100 targets.
        """
        raw_speeds = validate_u8_vector(speeds, count=self._SPEED_COUNT, name="speeds")

        # Build and send message
        data = [self._CONTROL_CMD, *raw_speeds]
        msg = can.Message(
            arbitration_id=self._arbitration_id,
            data=data,
            is_extended_id=False,
        )
        self._dispatcher.send(msg)
        self._relay.push(SpeedData(speeds=raw_speeds, timestamp=time.time()))

    def set_percentage_speeds(
        self, speeds: L6Speed | list[float] | tuple[float, ...]
    ) -> None:
        """Convert explicit 0..100 speed targets and send protocol values."""
        values = speeds.to_list() if isinstance(speeds, L6Speed) else speeds
        self.set_speeds(
            percentage_to_u8_vector(
                values, count=self._SPEED_COUNT, name="speed percentages"
            )
        )

    def get_snapshot(self) -> SpeedData | None:
        """Return the most recent target without performing device I/O.

        The L6 protocol does not support reading current speed, so no
        ``get_blocking`` method is provided for this manager.
        """
        return self._relay.snapshot()
