"""Transport-neutral enums shared by the hand SDK."""

from enum import Enum


class HandSide(str, Enum):
    """Physical side reported by or configured for a hand device."""

    LEFT = "left"
    RIGHT = "right"
    UNKNOWN = "unknown"


class TransportType(str, Enum):
    """Transport family used to communicate with a hand device."""

    CAN = "can"
    CAN_FD = "can_fd"
    FAKE = "fake"
    CUSTOM = "custom"


class SensorType(str, Enum):
    """Telemetry channel that can be scheduled by a polling plan."""

    ANGLES = "angles"
    SPEEDS = "speeds"
    TORQUES = "torques"
    FORCE = "force"
    TEMPERATURE = "temperature"
    CURRENT = "current"
    FAULTS = "faults"
    VOLTAGE = "voltage"
    ACCELERATION = "acceleration"
    MOTION_TIME = "motion_time"


class SensorValueKind(str, Enum):
    """Semantic shape of values published for a sensor channel."""

    NUMERIC = "numeric"
    BITMASK = "bitmask"
    ENUM = "enum"
    TACTILE_MATRIX = "tactile_matrix"


class CommandKind(str, Enum):
    """Operation accepted by the unified command scheduler."""

    ANGLES = "angles"
    SPEEDS = "speeds"
    TORQUES = "torques"
    CLEAR_FAULTS = "clear_faults"
    ENABLE = "enable"
    DISABLE = "disable"
    CALIBRATE = "calibrate"

    @property
    def is_continuous(self) -> bool:
        """Whether newer targets may supersede this command before dispatch."""
        return self in {
            CommandKind.ANGLES,
            CommandKind.SPEEDS,
            CommandKind.TORQUES,
        }


class CommandPolicy(str, Enum):
    """Queueing policy applied when a command enters the scheduler."""

    LATEST = "latest"
    FIFO = "fifo"


class CommandStage(str, Enum):
    """Observable stage in the command delivery lifecycle."""

    ACCEPTED = "accepted"
    DISPATCHED = "dispatched"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class AckSupport(str, Enum):
    """Whether a command kind has an explicit device-protocol ACK."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


class ConnectionState(str, Enum):
    """Current connection state exposed through :class:`HealthStatus`."""

    CLOSED = "closed"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class PercentageSemantics(str, Enum):
    """Meaning assigned to a joint's normalized 0 through 100 range."""

    OPENNESS = "openness"
    POSITION = "position"
    GRIP_POSE_INTERPOLATION = "grip_pose_interpolation"


class JointMotionType(str, Enum):
    """Mechanical motion category of a joint axis."""

    UNKNOWN = "unknown"
    FLEXION = "flexion"
    ABDUCTION = "abduction"
    ROLL = "roll"
    ROTATION = "rotation"
    WRIST = "wrist"


class JointEndpoint(str, Enum):
    """Named physical pose represented by one normalized range endpoint."""

    FLEXED = "flexed"
    EXTENDED = "extended"
    TOWARD_THUMB_WEB = "toward_thumb_web"
    AWAY_FROM_THUMB_WEB = "away_from_thumb_web"
    PARALLEL_TO_PALM = "parallel_to_palm"
    PERPENDICULAR_TO_PALM = "perpendicular_to_palm"
    TOWARD_MIDDLE_FINGER = "toward_middle_finger"
    AWAY_FROM_MIDDLE_FINGER = "away_from_middle_finger"
    TOWARD_INDEX_FINGER = "toward_index_finger"
    AWAY_FROM_INDEX_FINGER = "away_from_index_finger"
    TOWARD_THUMB = "toward_thumb"
    AWAY_FROM_THUMB = "away_from_thumb"
    TOWARD_LITTLE_FINGER = "toward_little_finger"
    AWAY_FROM_LITTLE_FINGER = "away_from_little_finger"
    TOWARD_PALM = "toward_palm"
    AWAY_FROM_PALM = "away_from_palm"
    FORWARD = "forward"
    BACKWARD = "backward"


__all__ = [
    "AckSupport",
    "CommandKind",
    "CommandPolicy",
    "CommandStage",
    "ConnectionState",
    "HandSide",
    "JointEndpoint",
    "JointMotionType",
    "PercentageSemantics",
    "SensorType",
    "SensorValueKind",
    "TransportType",
]
