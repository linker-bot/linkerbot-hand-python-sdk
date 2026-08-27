"""Raw stall-protection configuration samples."""

from dataclasses import dataclass

from .readings import _RawVectorSample


@dataclass(frozen=True, slots=True)
class StallDetectionTimeSample(_RawVectorSample):
    """Protocol values used by the device's stall-time configuration."""


@dataclass(frozen=True, slots=True)
class StallThresholdSample(_RawVectorSample):
    """Protocol values used by the device's stall-threshold configuration."""


@dataclass(frozen=True, slots=True)
class StallHoldCurrentSample(_RawVectorSample):
    """Protocol values used for current maintained after a detected stall."""


__all__ = [
    "StallDetectionTimeSample",
    "StallHoldCurrentSample",
    "StallThresholdSample",
]
