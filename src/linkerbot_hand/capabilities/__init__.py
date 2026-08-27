"""Model-specific capability declarations and lookup."""

from .registry import (
    SUPPORTED_MODELS,
    get_declared_capabilities,
)
from .types import (
    CommandCapability,
    CommandSemantic,
    DeviceCapabilities,
    IdentityCapability,
    JointCapability,
    NumericRange,
    OperationCapabilities,
    PercentMapping,
    PhysicalAngleMapping,
    StallFieldCapability,
    StallProtectionCapability,
    TelemetryCapability,
    TelemetrySemantic,
    TransportCapability,
)

__all__ = [
    "SUPPORTED_MODELS",
    "CommandCapability",
    "CommandSemantic",
    "DeviceCapabilities",
    "IdentityCapability",
    "JointCapability",
    "NumericRange",
    "OperationCapabilities",
    "PercentMapping",
    "PhysicalAngleMapping",
    "StallFieldCapability",
    "StallProtectionCapability",
    "TelemetryCapability",
    "TelemetrySemantic",
    "TransportCapability",
    "get_declared_capabilities",
]
