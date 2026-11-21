"""Declared O30 profile exposing only twenty installed protocol slots."""

from ...api.enums import (
    HandSide,
    JointMotionType,
)
from ..types import (
    DeviceCapabilities,
)
from ._common import (
    U8_RANGE,
    joint,
)

_RAW_ENCODING = "u8_v25_object"
_SENSOR_CHANNELS = ("thumb", "index", "middle", "ring", "pinky")

_JOINT_SPECS = (
    (
        "thumb_rotation",
        JointMotionType.ROTATION,
        "away_from_index_finger",
        "toward_index_finger",
    ),
    (
        "thumb_roll",
        JointMotionType.ROLL,
        "parallel_to_palm",
        "perpendicular_to_palm",
    ),
    ("index_abd", JointMotionType.ABDUCTION, "toward_thumb", "away_from_thumb"),
    ("middle_abd", JointMotionType.ABDUCTION, "toward_thumb", "away_from_thumb"),
    ("ring_abd", JointMotionType.ABDUCTION, "toward_thumb", "away_from_thumb"),
    ("pinky_abd", JointMotionType.ABDUCTION, "toward_thumb", "away_from_thumb"),
    ("thumb_root1", JointMotionType.FLEXION, "flexed", "extended"),
    ("index_root1", JointMotionType.FLEXION, "flexed", "extended"),
    ("middle_root1", JointMotionType.FLEXION, "flexed", "extended"),
    ("ring_root1", JointMotionType.FLEXION, "flexed", "extended"),
    ("pinky_root1", JointMotionType.FLEXION, "flexed", "extended"),
    ("index_root2", JointMotionType.FLEXION, "flexed", "extended"),
    ("middle_root2", JointMotionType.FLEXION, "flexed", "extended"),
    ("ring_root2", JointMotionType.FLEXION, "flexed", "extended"),
    ("pinky_root2", JointMotionType.FLEXION, "flexed", "extended"),
    ("thumb_tip", JointMotionType.FLEXION, "flexed", "extended"),
    ("index_tip", JointMotionType.FLEXION, "flexed", "extended"),
    ("middle_tip", JointMotionType.FLEXION, "flexed", "extended"),
    ("ring_tip", JointMotionType.FLEXION, "flexed", "extended"),
    ("pinky_tip", JointMotionType.FLEXION, "flexed", "extended"),
)


def declared_capabilities(side: HandSide) -> DeviceCapabilities:
    """Return immutable, unverified O30 metadata for ``side``."""

    joints = tuple(
        joint(
            index,
            name,
            motion_type,
            U8_RANGE,
            raw_at_zero=(255 if motion_type is JointMotionType.FLEXION else 0),
            raw_at_hundred=(0 if motion_type is JointMotionType.FLEXION else 255),
            zero_label=zero_label,
            hundred_label=hundred_label,
        )
        for index, (name, motion_type, zero_label, hundred_label) in enumerate(
            _JOINT_SPECS
        )
    )
    from ._hop import shared_capabilities

    return shared_capabilities(
        "O30",
        side,
        joints,
        raw_encoding=_RAW_ENCODING,
        addressing_note=(
            "HOP default standard-frame CAN ID pair: right hand request "
            "0x001 / response 0x401, left hand request 0x002 / response 0x402. "
            "CAN FD BRS disabled; nominal bitrate 1 Mbit/s; any interface data bitrate."
        ),
    )


__all__ = ["declared_capabilities"]
