"""O30i physical-joint definitions and normalized angle values."""

from __future__ import annotations

import math
from dataclasses import dataclass

from linkerbot_transport.errors import ValidationError

from . import protocol

O30I_JOINT_COUNT = protocol.O30I_CONTROLLED_JOINT_COUNT


@dataclass(frozen=True, slots=True)
class JointSpec:
    """One physical O30i joint and its public metadata.

    The measured protocol currently defines only a normalized byte range. It
    does not provide calibrated degrees. The public percentage direction is
    defined per axis by :class:`O30iAngle`.
    """

    id: str
    name: str
    part: str
    motion: str
    minimum: int = 0
    maximum: int = 255
    default: int = 128
    group: str = ""
    wire_slot: int = 0

    @property
    def min(self) -> int:
        """Return the JSON-compatible minimum field."""
        return self.minimum

    @property
    def max(self) -> int:
        """Return the JSON-compatible maximum field."""
        return self.maximum

    def to_dict(self) -> dict[str, str | int]:
        """Return the public six-field joint definition used by applications."""
        return {
            "id": self.id,
            "name": self.name,
            "min": self.minimum,
            "max": self.maximum,
            "default": self.default,
            "group": self.group,
        }


O30I_JOINT_SPECS: tuple[JointSpec, ...] = (
    JointSpec(
        "roll_0",
        "拇指旋转",
        "thumb",
        "rotation",
        default=128,
        group="旋转",
        wire_slot=0,
    ),
    JointSpec(
        "yaw_0", "拇指横滚", "thumb", "roll", default=50, group="横滚", wire_slot=5
    ),
    JointSpec(
        "yaw_1",
        "食指侧摆",
        "index",
        "abduction",
        default=50,
        group="侧摆",
        wire_slot=6,
    ),
    JointSpec(
        "yaw_2",
        "中指侧摆",
        "middle",
        "abduction",
        default=50,
        group="侧摆",
        wire_slot=7,
    ),
    JointSpec(
        "yaw_3",
        "无名指侧摆",
        "ring",
        "abduction",
        default=50,
        group="侧摆",
        wire_slot=8,
    ),
    JointSpec(
        "yaw_4",
        "小指侧摆",
        "pinky",
        "abduction",
        default=50,
        group="侧摆",
        wire_slot=9,
    ),
    JointSpec("root1_0", "拇指根 1", "thumb", "flexion", group="指根 1", wire_slot=10),
    JointSpec("root1_1", "食指根 1", "index", "flexion", group="指根 1", wire_slot=11),
    JointSpec("root1_2", "中指根 1", "middle", "flexion", group="指根 1", wire_slot=12),
    JointSpec("root1_3", "无名指根 1", "ring", "flexion", group="指根 1", wire_slot=13),
    JointSpec("root1_4", "小指根 1", "pinky", "flexion", group="指根 1", wire_slot=14),
    JointSpec("root2_1", "食指根 2", "index", "flexion", group="指根 2", wire_slot=16),
    JointSpec("root2_2", "中指根 2", "middle", "flexion", group="指根 2", wire_slot=17),
    JointSpec("root2_3", "无名指根 2", "ring", "flexion", group="指根 2", wire_slot=18),
    JointSpec("root2_4", "小指根 2", "pinky", "flexion", group="指根 2", wire_slot=19),
    JointSpec("tip_0", "拇指尖", "thumb", "flexion", group="指尖", wire_slot=20),
    JointSpec("tip_1", "食指尖", "index", "flexion", group="指尖", wire_slot=21),
    JointSpec("tip_2", "中指尖", "middle", "flexion", group="指尖", wire_slot=22),
    JointSpec("tip_3", "无名指尖", "ring", "flexion", group="指尖", wire_slot=23),
    JointSpec("tip_4", "小指尖", "pinky", "flexion", group="指尖", wire_slot=24),
)
O30I_DEFAULT_RAW_VALUES: tuple[int, ...] = tuple(
    spec.default for spec in O30I_JOINT_SPECS
)


@dataclass(frozen=True, slots=True)
class O30iAngle:
    """Twenty physical O30i joint values represented as percentages.

    Directional axes 0 through 5 map 0% and 100% to their measured low and high
    physical endpoints. Flexion axes 6 through 19 use the public openness
    convention: 0% is flexed (raw 255) and 100% is extended (raw 0). These are
    normalized endpoints, not calibrated angles in degrees.
    """

    values: tuple[float, ...]

    def __post_init__(self) -> None:
        _validate_percentage_values(self.values)

    @classmethod
    def from_list(cls, values: list[float] | tuple[float, ...]) -> O30iAngle:
        """Construct from 20 numeric percentages in public joint order."""
        return cls(tuple(values))

    @classmethod
    def from_raw(cls, values: list[int] | tuple[int, ...]) -> O30iAngle:
        """Construct from 20 protocol-native bytes using per-axis direction."""
        normalized = validate_u8_values(values, name="raw_angles")
        return cls(
            tuple(
                (value if index < 6 else 255 - value) * 100 / 255
                for index, value in enumerate(normalized)
            )
        )

    @classmethod
    def defaults(cls) -> O30iAngle:
        """Construct the device's documented default 20-joint pose."""
        return cls.from_raw(O30I_DEFAULT_RAW_VALUES)

    def to_list(self) -> list[float]:
        """Return a mutable copy of the normalized values."""
        return list(self.values)

    def to_raw(self) -> list[int]:
        """Convert logical percentages to per-axis protocol-native bytes."""
        return [
            round((value if index < 6 else 100 - value) * 255 / 100)
            for index, value in enumerate(self.values)
        ]

    def __getitem__(self, index: int) -> float:
        return self.values[index]

    def __len__(self) -> int:
        return O30I_JOINT_COUNT


def validate_u8_values(
    values: list[int] | tuple[int, ...],
    *,
    name: str,
) -> tuple[int, ...]:
    """Validate one complete 20-joint byte vector."""
    normalized = tuple(values)
    if len(normalized) != O30I_JOINT_COUNT:
        raise ValidationError(
            f"{name} must contain {O30I_JOINT_COUNT} values, got {len(normalized)}"
        )
    for index, value in enumerate(normalized):
        protocol._validate_u8(value, f"{name}[{index}]")
    return normalized


def _validate_percentage_values(values: tuple[float, ...]) -> None:
    if len(values) != O30I_JOINT_COUNT:
        raise ValidationError(
            f"angles must contain {O30I_JOINT_COUNT} values, got {len(values)}"
        )
    for index, value in enumerate(values):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValidationError(f"angles[{index}] must be float/int")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValidationError(f"angles[{index}] must be finite, got {value}")
        if value < 0 or value > 100:
            raise ValidationError(
                f"angles[{index}] must be between 0 and 100, got {value}"
            )
