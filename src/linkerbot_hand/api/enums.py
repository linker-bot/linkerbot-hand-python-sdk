"""Small enums shared by the Hand SDK 1.0 public contracts."""

from enum import Enum


class HandSide(str, Enum):
    """Physical side of a hand device."""

    LEFT = "left"
    RIGHT = "right"
    NOT_APPLICABLE = "not_applicable"


class DataOrigin(str, Enum):
    """How a cached sample entered the canonical snapshot."""

    NONE = "none"
    DEVICE_RESPONSE = "device_response"
    DEVICE_REPORT = "device_report"
    DEVICE_CONFIG_RESPONSE = "device_config_response"
    DEVICE_COMMAND_FEEDBACK = "device_command_feedback"
    COMMAND_CACHE = "command_cache"


class AngleFeedbackPolicy(str, Enum):
    """How periodic angle feedback combines queries and command responses."""

    QUERY_ONLY = "query_only"
    COMMAND_FEEDBACK_ONLY = "command_feedback_only"
    ADAPTIVE = "adaptive"


class Confidence(str, Enum):
    """Evidence level attached to capability metadata."""

    MEASURED = "measured"
    DOCUMENTED = "documented"
    APPROXIMATE = "approximate"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


class JointMotionType(str, Enum):
    """Mechanical motion category of a public joint axis."""

    UNKNOWN = "unknown"
    FLEXION = "flexion"
    ABDUCTION = "abduction"
    ROLL = "roll"
    ROTATION = "rotation"
    WRIST = "wrist"


class TransportKind(str, Enum):
    """Physical transport family required by a device profile."""

    CAN = "can"
    CAN_FD = "can_fd"
    FAKE = "fake"
    CUSTOM = "custom"


class ReadMode(str, Enum):
    """Ways a protocol can deliver one telemetry channel."""

    QUERY = "query"
    REPORT = "report"
    QUERY_AND_REPORT = "query_and_report"
    COMMAND_CACHE = "command_cache"
    COMMAND_RESPONSE = "command_response"


class SampleProjection(str, Enum):
    """Which sample fields a telemetry channel populates.

    Consumers must read this instead of reverse-engineering ``raw_encoding`` or
    counting ``shape`` dimensions: those describe the wire format, which is
    independent of which sample field the protocol profile fills.
    """

    RAW_VECTOR = "raw_vector"
    TACTILE_MATRIX = "tactile_matrix"
    TACTILE_DESCRIPTOR = "tactile_descriptor"


class Quantization(str, Enum):
    """Rule used when a percent conversion targets an integer protocol field."""

    NEAREST_TIES_AWAY_FROM_ZERO = "nearest_ties_away_from_zero"
    FLOOR = "floor"
    CEIL = "ceil"


__all__ = [
    "AngleFeedbackPolicy",
    "Confidence",
    "DataOrigin",
    "HandSide",
    "JointMotionType",
    "Quantization",
    "ReadMode",
    "SampleProjection",
    "TransportKind",
]
