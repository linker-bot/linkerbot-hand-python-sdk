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


@dataclass(frozen=True, slots=True)
class StallHoldTorqueSample(_RawVectorSample):
    """Raw output torque maintained after a stall; no physical conversion."""


@dataclass(frozen=True, slots=True)
class LimitCompensationSample(_RawVectorSample):
    """Raw joint-limit compensation values returned by the device."""


__all__ = [
    "LimitCompensationSample",
    "StallDetectionTimeSample",
    "StallHoldCurrentSample",
    "StallHoldTorqueSample",
    "StallThresholdSample",
]
