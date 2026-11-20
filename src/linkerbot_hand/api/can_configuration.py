"""HOP device-side CAN configuration, independent of adapter settings."""

from dataclasses import dataclass

from .errors import ValidationError

NOMINAL_BITRATES = (
    1_000_000,
    800_000,
    500_000,
    400_000,
    250_000,
    200_000,
    125_000,
    100_000,
)
DATA_BITRATES = (
    8_000_000,
    5_000_000,
    4_000_000,
    3_000_000,
    2_000_000,
    1_000_000,
    800_000,
    500_000,
    400_000,
    250_000,
    200_000,
    125_000,
    100_000,
)


@dataclass(frozen=True, slots=True)
class CANConfiguration:
    """Device register values; these do not reconfigure the host adapter."""

    request_id: int
    interface_type: int
    brs: bool
    nominal_bitrate: int
    data_bitrate: int

    def __post_init__(self) -> None:
        validate_can_id(self.request_id)
        if type(self.interface_type) is not int or self.interface_type not in (1, 2):
            raise ValidationError("CAN interface_type must be 1 or 2")
        if type(self.brs) is not bool:
            raise ValidationError("brs must be bool")
        bitrate_code(self.nominal_bitrate, data=False)
        bitrate_code(self.data_bitrate, data=True)

    @property
    def response_id(self) -> int:
        """Return the derived standard response ID."""
        return self.request_id | 0x400


def validate_can_id(value: int) -> None:
    if type(value) is not int or not 0 <= value <= 0x3FE:
        raise ValidationError("CAN request ID must be int in 0x000..0x3FE")


def bitrate_code(value: int, *, data: bool) -> int:
    allowed = DATA_BITRATES if data else NOMINAL_BITRATES
    if type(value) is not int or value not in allowed:
        raise ValidationError(
            f"unsupported {'data' if data else 'nominal'} bitrate: {value!r}"
        )
    return allowed.index(value) + 1


__all__ = ["CANConfiguration"]
